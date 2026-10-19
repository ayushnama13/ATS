"""Weighted ranking over cached features, with switchable factors and hard filters."""
from .features import FACTORS

DEFAULT_WEIGHTS = {
    "must_skill": 20, "nice_skill": 8, "keyword": 7, "tfidf": 5, "semantic": 15, "experience": 8,
    "projects": 6, "impact": 7, "leadership": 5, "cgpa": 8, "program_fit": 3, "branch_fit": 5,
    "ats_format": 3, "llm": 30,
}
DEFAULT_ENABLED = {f: True for f in FACTORS}


def score_one(features, weights, enabled):
    """Weighted mean over enabled factors that apply to this candidate (None factors drop out)."""
    num = den = 0.0
    parts = {}
    for f in FACTORS:
        v, w = features.get(f), float(weights.get(f, 0))
        if v is None or not enabled.get(f, True) or w <= 0:
            parts[f] = {"value": v, "weight": 0.0, "points": 0.0}
            continue
        num += v * w
        den += w
        parts[f] = {"value": v, "weight": w, "points": 0.0}
    for f, p in parts.items():
        if den:
            p["points"] = round(100 * p["value"] * p["weight"] / den, 2) if p["weight"] else 0.0
    return (100 * num / den if den else 0.0), parts


def passes(c, flt):
    if flt.get("min_cgpa") is not None and (c["cgpa"] is None or c["cgpa"] < flt["min_cgpa"]):
        return False
    if flt.get("programs") and c["program"] not in flt["programs"]:
        return False
    if flt.get("departments") and c["dept"] not in flt["departments"]:
        return False
    if flt.get("exclude_placed") and (c.get("placed") or "").lower() == "placed":
        return False
    if flt.get("min_words") and c["words"] < flt["min_words"]:
        return False
    return True


def _matches_search(r, q):
    return q.lower() in (r["name"] + " " + r["roll"] + " " + r["dept"] + " " + r["spec"]).lower()


def rank(candidates, feats, weights=None, enabled=None, flt=None):
    """candidates and feats are parallel lists. Returns rows sorted best-first with rank set."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    enabled = {**DEFAULT_ENABLED, **(enabled or {})}
    flt = flt or {}
    rows = []
    for c, ft in zip(candidates, feats):
        if not passes(c, flt):
            continue
        s, parts = score_one(ft["features"], weights, enabled)
        rows.append({"roll": c["roll"], "name": c["name"], "program": c["program"], "dept": c["dept"],
                     "spec": c["spec"], "cgpa": c["cgpa"], "placed": c.get("placed", ""),
                     "score": round(s, 2), "parts": parts, "detail": ft["detail"]})
    rows.sort(key=lambda r: (-r["score"], r["roll"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    if flt.get("search"):   # search only narrows what is shown; ranks stay relative to the whole pool
        rows = [r for r in rows if _matches_search(r, flt["search"])]
    return rows
