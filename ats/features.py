"""Per-candidate features, each normalised to 0..1 (None = not applicable for this JD)."""
import re

import numpy as np
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer

from . import llm, sections, skills, store

FACTORS = ["must_skill", "nice_skill", "keyword", "tfidf", "semantic", "experience", "projects",
           "impact", "leadership", "cgpa", "program_fit", "branch_fit", "ats_format", "llm"]

_INTERN = re.compile(r"\b(intern|internship|summer analyst|research assistant|trainee|apprentice)\b", re.I)
_LEAD = skills._skill_pattern("leadership")
_ENTRE = skills._skill_pattern("entrepreneurship")


def _percentile(vals):
    """Rank-normalise to 0..1 so cohort-relative signals (tfidf, embeddings) spread out."""
    a = np.asarray(vals, dtype=float)
    if len(a) < 2:
        return [0.5] * len(a)
    ranks = a.argsort().argsort()
    return list(ranks / (len(a) - 1))


def _skill_cov(text, skills_text, wanted):
    """Coverage: 1.0 if evidenced outside the skills list, 0.6 if only listed."""
    if not wanted:
        return None, [], []
    body = text.replace(skills_text, "") if skills_text else text
    matched, missing, score = [], [], 0.0
    for s in wanted:
        if skills.has_skill(body, s):
            score += 1.0
            matched.append(s)
        elif skills.has_skill(text, s):
            score += 0.6
            matched.append(s + " (listed only)")
        else:
            missing.append(s)
    return score / len(wanted), matched, missing


def _program_fit(c, prof):
    prefs = prof.get("preferred_programs") or []
    if not prefs:
        return None
    prog = re.sub(r"[^a-z]", "", c["program"].lower())
    for p in prefs:
        pn = re.sub(r"[^a-z]", "", p.lower())
        if pn and (pn in prog or prog in pn):
            return 1.0
    return 0.4 if "dual" in prog or "mtech" in prog else 0.2


def _branch_fit(c, prof):
    prefs = prof.get("preferred_departments") or []
    hay = f"{c['dept']} {c['spec']}".lower()
    if not prefs:
        return None
    best = max((fuzz.partial_ratio(p.lower(), hay) for p in prefs), default=0) / 100
    return 1.0 if best >= 0.9 else best * 0.8


def _ats_format(a, c):
    """Checklist score plus human-readable issues."""
    score, flags = 0.0, []
    checks = [
        (c["words"] >= 150, 2, "very little text extracted (scanned or image-only?)"),
        ("education" in a["sections"], 1, "no Education section detected"),
        (bool({"experience", "projects"} & set(a["sections"])), 2, "no Experience/Projects section detected"),
        ("skills" in a["sections"], 1, "no Skills section detected"),
        (a["bullets"] >= 6, 1, "few bullet points (<6)"),
        (150 <= c["words"] <= 1100, 1, "length outside 150-1100 words"),
        (c["pages"] <= 2, 1, f"{c['pages']} pages (keep to 1-2)"),
        (a["has_links"], 0.5, "no GitHub/LinkedIn/portfolio link"),
    ]
    total = sum(w for _, w, _ in checks)
    for ok, w, msg in checks:
        if ok:
            score += w
        else:
            flags.append(msg)
    return score / total, flags


def compute(candidates, prof, embeddings=True, llm_results=None):
    """Return list of {roll, features, detail}. llm_results: {roll: rubric json} from cache."""
    llm_results = llm_results or {}
    jd_text = prof.get("jd_text", "")
    kw = prof.get("keywords", [])
    texts = [c["text"] or " " for c in candidates]

    # cohort-relative signals
    tf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=2, max_df=0.9)
    mat = tf.fit_transform(texts + [jd_text + " " + " ".join(kw)])
    sims = (mat[:-1] @ mat[-1].T).toarray().ravel()
    tfidf_pct = _percentile(sims)

    sem_pct = [None] * len(candidates)
    if embeddings and llm.available():
        try:
            jd_doc = " ".join([prof.get("summary", ""), *prof.get("signals", []),
                               *[f"{x['name']}: {x['description']}" for x in prof.get("competencies", [])], jd_text[:3000]])
            vecs = np.array(llm.embed([jd_doc] + [t[:8000] for t in texts]))
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
            sem_pct = _percentile(vecs[1:] @ vecs[0])
        except llm.LLMError:
            pass

    out = []
    for i, c in enumerate(candidates):
        text = c["text"]
        a = sections.analyse(text, c["pages"], c["words"])
        must, m_hit, m_miss = _skill_cov(text, a["skills_text"], prof.get("must_have_skills"))
        nice, n_hit, n_miss = _skill_cov(text, a["skills_text"], prof.get("nice_to_have_skills"))
        kw_hits = [k for k in kw if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text, re.I)]
        secs = sections.split_sections(text)
        exp_text, proj_text = secs.get("experience", ""), secs.get("projects", "")
        n_intern = len(_INTERN.findall(text))
        exp_bul = sum(1 for l in exp_text.splitlines() if sections._BULLET.match(l))
        proj_bul = sum(1 for l in proj_text.splitlines() if sections._BULLET.match(l))
        b = max(a["bullets"], 1)
        lead_hits = len(_LEAD.findall(secs.get("positions", ""))) + len(_ENTRE.findall(text))
        fmt, flags = _ats_format(a, c)
        rub = llm_results.get(c["roll"])
        f = {
            "must_skill": must,
            "nice_skill": nice,
            "keyword": min(1.0, len(kw_hits) / max(1, 0.5 * len(kw))) if kw else None,
            "tfidf": tfidf_pct[i],
            "semantic": sem_pct[i],
            "experience": min(1.0, 0.55 * min(n_intern, 2) / 2 + 0.45 * min(exp_bul, 10) / 10),
            "projects": min(1.0, min(proj_bul, 12) / 12),
            "impact": min(1.0, 0.65 * min(1.0, a["quantified_bullets"] / b / 0.5) + 0.35 * min(1.0, a["action_bullets"] / b / 0.7)),
            "leadership": min(1.0, lead_hits / 5),
            "cgpa": None if c["cgpa"] is None else max(0.0, min(1.0, (c["cgpa"] - 6.0) / 3.5)),
            "program_fit": _program_fit(c, prof),
            "branch_fit": _branch_fit(c, prof),
            "ats_format": fmt,
            "llm": None if not rub else max(0.0, min(1.0, rub.get("fit_score", 0) / 100)),
        }
        out.append({
            "roll": c["roll"], "features": f,
            "detail": {"matched_must": m_hit, "missing_must": m_miss, "matched_nice": n_hit, "missing_nice": n_miss,
                       "keywords_hit": kw_hits, "format_flags": flags, "interns": n_intern,
                       "quantified_bullets": a["quantified_bullets"], "bullets": a["bullets"],
                       "sections": a["sections"], "rubric": rub},
        })
    return out
