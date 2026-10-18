"""Gemini client: JSON generation, PDF OCR and embeddings. Responses are cached by prompt hash."""
import base64
import hashlib
import json
import re
import threading
import time
from collections import deque

import httpx

from . import config, store


class LLMError(RuntimeError):
    pass


def available():
    return bool(config.GEMINI_API_KEY)


_rl_lock = threading.Lock()
_rl_calls = {}  # model -> deque of recent call timestamps


def _throttle(path):
    """Sliding 60s window per model so parallel workers share the free-tier requests/minute quota."""
    key = path.split(":")[0]
    while True:
        with _rl_lock:
            dq = _rl_calls.setdefault(key, deque())
            now = time.monotonic()
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) < config.LLM_RPM:
                dq.append(now)
                return
            wait = 60 - (now - dq[0]) + 0.05
        time.sleep(max(wait, 0.05))


def _post(path, body, timeout=120, retries=6):
    if not available():
        raise LLMError("GEMINI_API_KEY not set")
    url = f"{config.GEMINI_BASE}/{path}"
    last = None
    for i in range(retries):
        _throttle(path)
        try:
            r = httpx.post(url, json=body, params={"key": config.GEMINI_API_KEY}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 429:
                m = re.search(r"retry in ([\d.]+)s", r.text)
                time.sleep(min(float(m.group(1)) + 1 if m else 30, 70))
                continue
            if r.status_code not in (500, 502, 503, 504):
                break
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(2 ** i, 20))
    raise LLMError(last or "request failed")


def _text(resp):
    cands = resp.get("candidates") or []
    if not cands:
        raise LLMError(f"empty response: {json.dumps(resp)[:200]}")
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _parse_json(txt):
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            return json.loads(m.group(0))
        raise LLMError("model did not return JSON")


def generate_json(prompt, cache=True, model=None):
    model = model or config.GEMINI_MODEL
    key = "llm:" + hashlib.sha1((model + "\n" + prompt).encode()).hexdigest()
    if cache:
        hit = store.get(key)
        if hit is not None:
            return hit
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    out = _parse_json(_text(_post(f"models/{model}:generateContent", body)))
    if cache:
        store.put(key, out)
    return out


def ocr_pdf(pdf_bytes):
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}},
        {"text": "Transcribe all text in this resume exactly as plain text, preserving reading order. "
                 "Output only the text."}]}],
        "generationConfig": {"temperature": 0}}
    return _text(_post(f"models/{config.GEMINI_MODEL}:generateContent", body, timeout=180))


def _emb_key(t):
    return "emb:" + config.GEMINI_EMBED_MODEL + ":" + hashlib.sha1(t[:config.EMBED_CHARS].encode()).hexdigest()


def embed_cached(texts):
    """Vectors for texts if ALL are cached, else None (never hits the network)."""
    out = [store.get(_emb_key(t)) for t in texts]
    return None if any(v is None for v in out) else out


def embed(texts, task="SEMANTIC_SIMILARITY", progress=None):
    """One vector per text. Paced by estimated tokens to stay under the free-tier tokens/minute quota."""
    out, todo = {}, []
    for i, t in enumerate(texts):
        hit = store.get(_emb_key(t))
        if hit is not None:
            out[i] = hit
        else:
            todo.append((i, t[:config.EMBED_CHARS]))
    step = 10
    for s in range(0, len(todo), step):
        t0 = time.monotonic()
        batch = todo[s:s + step]
        body = {"requests": [{"model": f"models/{config.GEMINI_EMBED_MODEL}",
                              "content": {"parts": [{"text": t}]}, "taskType": task} for _, t in batch]}
        resp = _post(f"models/{config.GEMINI_EMBED_MODEL}:batchEmbedContents", body, retries=10)
        for (i, t), e in zip(batch, resp["embeddings"]):
            out[i] = e["values"]
            store.put(_emb_key(t), e["values"])
        if progress:
            progress(min(s + step, len(todo)), len(todo))
        if s + step < len(todo):
            tokens = sum(len(t) for _, t in batch) / 3.5
            time.sleep(max(0.0, tokens * 60 / config.EMBED_TPM - (time.monotonic() - t0)))
    return [out[i] for i in range(len(texts))]
