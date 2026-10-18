"""LLM rubric scoring: Gemini scores each resume against the JD's competencies, with evidence."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from . import config, llm, store

BATCH = 3

PROMPT = """You are a strict, fair campus-recruitment screener. Score ONE resume against the role below.
Judge only evidence present in the resume text. Ignore name, gender, college prestige and formatting beauty.
Calibration: 50 = typical applicant for this role, 70 = clearly good, 85+ = exceptional (rare). Be harsh with
vague claims; reward concrete, quantified, relevant work. A missing area scores 0-2, not 5.
If your evidence for a competency is "none", its score MUST be 0-2.

ROLE: {title} at {company}
SUMMARY: {summary}
COMPETENCIES (score each 0-10): {comps}
STRONG SIGNALS: {signals}
MUST-HAVE SKILLS: {must}
CANDIDATE: program={program}; department={dept}; specialization={spec}; CGPA={cgpa}

RESUME:
{resume}

Return ONLY JSON:
{{"fit_score": 0-100 overall fit for this role,
 "competency_scores": [{{"name": <exact competency name>, "score": 0-10, "evidence": "<=25 words from resume or 'none'"}}],
 "strengths": [<=3 short strings], "gaps": [<=3 short strings], "red_flags": [short strings or empty],
 "summary": "<=30 words recruiter-style verdict"}}
"""


def profile_hash(prof):
    keys = {k: prof.get(k) for k in ("role_title", "summary", "competencies", "signals", "must_have_skills")}
    return hashlib.sha1(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:12]


def _combine(res, prof):
    """Blend the model's holistic score with the weight-averaged competency scores to cut noise."""
    comps = {c["name"]: c.get("weight", 3) for c in prof.get("competencies", [])}
    num = den = 0.0
    for cs in res.get("competency_scores", []):
        w = comps.get(cs.get("name"), 3)
        try:
            if str(cs.get("evidence", "")).strip().lower().startswith("none"):
                cs["score"] = min(float(cs.get("score", 0)), 2.0)  # no evidence, no credit
            num += float(cs.get("score", 0)) * w
            den += w
        except (TypeError, ValueError):
            pass
    holistic = float(res.get("fit_score", 0) or 0)
    res["fit_score"] = round(0.5 * holistic + 0.5 * (10 * num / den) if den else holistic, 1)
    res["holistic_score"] = holistic
    return res


def score_candidate(c, prof, slug):
    key = f"rubric:{slug}:{c['roll']}"
    ph = profile_hash(prof)
    hit = store.get(key)
    if hit and hit.get("ph") == ph and hit.get("words") == c["words"]:
        return hit["result"]
    prompt = PROMPT.format(
        title=prof.get("role_title", ""), company=prof.get("company", slug), summary=prof.get("summary", ""),
        comps="; ".join(f"{x['name']} (weight {x.get('weight', 3)}): {x['description']}" for x in prof.get("competencies", [])),
        signals="; ".join(prof.get("signals", [])), must=", ".join(prof.get("must_have_skills", [])) or "none stated",
        program=c["program"], dept=c["dept"], spec=c["spec"], cgpa=c["cgpa"], resume=c["text"][:9000])
    res = _combine(llm.generate_json(prompt), prof)
    store.put(key, {"ph": ph, "words": c["words"], "result": res})
    return res


BATCH_PROMPT = PROMPT.split("CANDIDATE:")[0] + """
You will receive several candidates. Score EACH independently; do not compare them with one another.
{cands}

Return ONLY JSON: {{"results": [ {{"id": "<candidate id>", "fit_score": 0-100,
 "competency_scores": [{{"name": <exact competency name>, "score": 0-10, "evidence": "<=25 words from resume or 'none'"}}],
 "strengths": [<=3 short strings], "gaps": [<=3 short strings], "red_flags": [short strings or empty],
 "summary": "<=30 words recruiter-style verdict"}} ]}}
"""


def score_batch(batch, prof, slug):
    """Score several candidates in one request (saves quota); returns {roll: result}."""
    ph = profile_hash(prof)
    ids = {f"C{i + 1}": c for i, c in enumerate(batch)}
    cands = "\n\n".join(
        f"=== CANDIDATE {i}: program={c['program']}; department={c['dept']}; specialization={c['spec']}; CGPA={c['cgpa']}\n"
        f"{c['text'][:6500]}" for i, c in ids.items())
    prompt = BATCH_PROMPT.format(
        title=prof.get("role_title", ""), company=prof.get("company", slug), summary=prof.get("summary", ""),
        comps="; ".join(f"{x['name']} (weight {x.get('weight', 3)}): {x['description']}" for x in prof.get("competencies", [])),
        signals="; ".join(prof.get("signals", [])), must=", ".join(prof.get("must_have_skills", [])) or "none stated",
        cands=cands)
    out = {}
    for item in llm.generate_json(prompt).get("results", []):
        c = ids.get(item.get("id"))
        if c and item.get("competency_scores"):
            res = _combine(item, prof)
            store.put(f"rubric:{slug}:{c['roll']}", {"ph": ph, "words": c["words"], "result": res})
            out[c["roll"]] = res
    return out


def load_cached(slug, candidates, prof):
    ph = profile_hash(prof)
    out = {}
    for c in candidates:
        hit = store.get(f"rubric:{slug}:{c['roll']}")
        if hit and hit.get("ph") == ph and hit.get("words") == c["words"]:
            out[c["roll"]] = hit["result"]
    return out


def run(slug, candidates, prof, rolls=None, progress=None):
    """Score candidates (all, or just `rolls`) with a thread pool; returns {roll: result} and error list."""
    cached = load_cached(slug, candidates, prof)
    todo = [c for c in candidates if (rolls is None or c["roll"] in rolls) and c["words"] >= 30]
    results = {c["roll"]: cached[c["roll"]] for c in todo if c["roll"] in cached}
    todo = [c for c in todo if c["roll"] not in cached]
    errors, done, total = [], len(results), len(results) + len(todo)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    def work(batch):
        try:
            got = score_batch(batch, prof, slug)
            for c in batch:  # anything the model skipped is retried on its own
                if c["roll"] not in got:
                    got[c["roll"]] = score_candidate(c, prof, slug)
            return got, []
        except Exception as e:
            return {}, [(c["roll"], f"{type(e).__name__}: {e}") for c in batch]

    if progress:
        progress(done, total)
    with ThreadPoolExecutor(max_workers=config.LLM_CONCURRENCY) as ex:
        for got, errs in ex.map(work, batches):
            results.update(got)
            errors.extend(errs)
            done += len(got) + len(errs)
            if progress:
                progress(done, total)
    return results, errors
