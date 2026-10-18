"""Gemini client: JSON generation, PDF OCR and embeddings. Responses are cached by prompt hash."""
import base64
import hashlib
import json
import re
import time

import httpx

from . import config, store


class LLMError(RuntimeError):
    pass


def available():
    return bool(config.GEMINI_API_KEY)


def _post(path, body, timeout=120, retries=5):
    if not available():
        raise LLMError("GEMINI_API_KEY not set")
    url = f"{config.GEMINI_BASE}/{path}"
    last = None
    for i in range(retries):
        try:
            r = httpx.post(url, json=body, params={"key": config.GEMINI_API_KEY}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
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


def embed(texts, task="SEMANTIC_SIMILARITY"):
    """Return one vector per text (cached per text)."""
    out, todo = {}, []
    for i, t in enumerate(texts):
        k = "emb:" + config.GEMINI_EMBED_MODEL + ":" + hashlib.sha1(t.encode()).hexdigest()
        hit = store.get(k)
        if hit is not None:
            out[i] = hit
        else:
            todo.append((i, k, t))
    for s in range(0, len(todo), 50):
        batch = todo[s:s + 50]
        body = {"requests": [{"model": f"models/{config.GEMINI_EMBED_MODEL}",
                              "content": {"parts": [{"text": t[:8000]}]}, "taskType": task}
                             for _, _, t in batch]}
        resp = _post(f"models/{config.GEMINI_EMBED_MODEL}:batchEmbedContents", body)
        for (i, k, _), e in zip(batch, resp["embeddings"]):
            out[i] = e["values"]
            store.put(k, e["values"])
    return [out[i] for i in range(len(texts))]
