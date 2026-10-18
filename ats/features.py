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


def jd_embedding_text(prof):
    return " ".join([prof.get("summary", ""), *prof.get("signals", []),
                     *[f"{x['name']}: {x['description']}" for x in prof.get("competencies", [])],
                     prof.get("jd_text", "")[:3000]])


class Context:
    """Cohort-level state: lets cohort-relative signals (tfidf, embeddings) be scored for new resumes too."""

    def __init__(self, candidates, prof, embeddings=True):
        self.prof = prof
        kw = prof.get("keywords", [])
        texts = [c["text"] or " " for c in candidates]
        self.tf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=2, max_df=0.9)
        mat = self.tf.fit_transform(texts)
        self.jd_vec = self.tf.transform([prof.get("jd_text", "") + " " + " ".join(kw)])
        self.tf_sims = (mat @ self.jd_vec.T).toarray().ravel()
        self.sem_sims, self.jd_emb = None, None
        if embeddings and llm.available():
            jd_doc = jd_embedding_text(prof)
            docs = [jd_doc] + [t[:8000] for t in texts]
            try:
                # "cached" never blocks on the network; True fetches whatever is missing (slow first time)
                vecs = llm.embed_cached(docs) if embeddings == "cached" else llm.embed(docs)
            except llm.LLMError:
                vecs = None
            if vecs is not None:
                vecs = self._norm(np.array(vecs))
                self.jd_emb, self.sem_sims = vecs[0], vecs[1:] @ vecs[0]

    @staticmethod
    def _norm(v):
        return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)

    @staticmethod
    def _pct(value, arr):
        arr = np.asarray(arr)
        return float((arr < value).sum() + 0.5 * (arr == value).sum()) / max(len(arr), 1)

    def tfidf_pct(self, i=None, text=None):
        if i is not None:
            return self._pct(self.tf_sims[i], self.tf_sims)
        sim = (self.tf.transform([text]) @ self.jd_vec.T).toarray().ravel()[0]
        return self._pct(sim, self.tf_sims)

    def semantic_pct(self, i=None, text=None):
        if self.sem_sims is None:
            return None
        if i is not None:
            return self._pct(self.sem_sims[i], self.sem_sims)
        v = self._norm(np.array(llm.embed([text[:8000]])[0]))
        return self._pct(float(v @ self.jd_emb), self.sem_sims)


def candidate_features(c, prof, ctx, i=None, rubric=None):
    """Features + detail for one candidate; i is the cohort index (None for an outside resume)."""
    text, kw = c["text"], prof.get("keywords", [])
    a = sections.analyse(text, c["pages"], c["words"])
    must, m_hit, m_miss = _skill_cov(text, a["skills_text"], prof.get("must_have_skills"))
    nice, n_hit, n_miss = _skill_cov(text, a["skills_text"], prof.get("nice_to_have_skills"))
    kw_hits = [k for k in kw if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text, re.I)]
    secs = sections.split_sections(text)
    n_intern = len(_INTERN.findall(text))
    exp_bul = sum(1 for l in secs.get("experience", "").splitlines() if sections._BULLET.match(l))
    proj_bul = sum(1 for l in secs.get("projects", "").splitlines() if sections._BULLET.match(l))
    b = max(a["bullets"], 1)
    lead_hits = len(_LEAD.findall(secs.get("positions", ""))) + len(_ENTRE.findall(text))
    fmt, flags = _ats_format(a, c)
    f = {
        "must_skill": must,
        "nice_skill": nice,
        "keyword": min(1.0, len(kw_hits) / max(1, 0.5 * len(kw))) if kw else None,
        "tfidf": ctx.tfidf_pct(i, text),
        "semantic": ctx.semantic_pct(i, text),
        "experience": min(1.0, 0.55 * min(n_intern, 2) / 2 + 0.45 * min(exp_bul, 10) / 10),
        "projects": min(1.0, min(proj_bul, 12) / 12),
        "impact": min(1.0, 0.65 * min(1.0, a["quantified_bullets"] / b / 0.5) + 0.35 * min(1.0, a["action_bullets"] / b / 0.7)),
        "leadership": min(1.0, lead_hits / 5),
        "cgpa": None if c["cgpa"] is None else max(0.0, min(1.0, (c["cgpa"] - 6.0) / 3.5)),
        "program_fit": _program_fit(c, prof),
        "branch_fit": _branch_fit(c, prof),
        "ats_format": fmt,
        "llm": None if not rubric else max(0.0, min(1.0, rubric.get("fit_score", 0) / 100)),
    }
    return {"roll": c["roll"], "features": f,
            "detail": {"matched_must": m_hit, "missing_must": m_miss, "matched_nice": n_hit, "missing_nice": n_miss,
                       "keywords_hit": kw_hits, "keywords_missing": [k for k in kw if k not in kw_hits],
                       "format_flags": flags, "interns": n_intern, "quantified_bullets": a["quantified_bullets"],
                       "bullets": a["bullets"], "sections": a["sections"], "rubric": rubric}}


def compute(candidates, prof, embeddings=True, llm_results=None, ctx=None):
    """Return (list of {roll, features, detail}, ctx). llm_results: {roll: rubric json} from cache."""
    llm_results = llm_results or {}
    ctx = ctx or Context(candidates, prof, embeddings)
    return [candidate_features(c, prof, ctx, i, llm_results.get(c["roll"])) for i, c in enumerate(candidates)], ctx
