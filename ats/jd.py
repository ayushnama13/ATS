"""Turn a JD into a structured profile (skills, competencies, preferences) that drives scoring."""
import re

from . import ingest, llm, skills

PROMPT = """You are building an applicant-screening rubric from a campus-hiring job description.
Return ONLY JSON with this exact shape:
{{
 "role_title": str, "company": str, "summary": "<=40 words on what the job is",
 "must_have_skills": [str],  // concrete technical/tool skills the JD requires (short names, e.g. "SQL", "Tableau", "Python")
 "nice_to_have_skills": [str],
 "competencies": [  // 5-8 evaluable qualities, INCLUDING soft/product ones the JD implies even if not a tool
   {{"name": str, "description": "what evidence in a resume shows this", "weight": 1-5}}
 ],
 "signals": [str],  // concrete resume evidence that should raise a candidate (e.g. "built and shipped a product", "case competition win")
 "preferred_programs": [str],  // e.g. "M.Tech","M.S","M.Sc","M.A.","Dual Degree","B.Tech" - [] if the JD states none
 "preferred_departments": [str],  // [] if unrestricted
 "min_cgpa": number|null,
 "keywords": [str]  // 15-30 important terms/phrases from the JD for text matching
}}
Be faithful to the JD; do not invent requirements. If the JD names few tools, lean on competencies.

JOB DESCRIPTION:
{jd}
"""


def _heuristic(text):
    found = skills.find_skills(text)
    ranked = sorted(found, key=lambda s: -found[s])
    return {
        "role_title": "", "company": "", "summary": text[:200],
        "must_have_skills": ranked[:6], "nice_to_have_skills": ranked[6:14],
        "competencies": [{"name": s, "description": f"evidence of {s}", "weight": 3} for s in ranked[:6]],
        "signals": [], "preferred_programs": [], "preferred_departments": [], "min_cgpa": None,
        "keywords": ranked[:25], "source": "heuristic",
    }


def build_profile(slug, force=False):
    existing = ingest.load_jd_profile(slug)
    if existing and not force:
        return existing
    text = ingest.jd_text(slug)
    # drop boilerplate disclaimers that mention many skills/terms
    text = re.split(r"\n\s*Disclaimer", text, flags=re.I)[0]
    if llm.available():
        try:
            prof = llm.generate_json(PROMPT.format(jd=text[:12000]))
            prof["source"] = "llm"
        except llm.LLMError:
            prof = _heuristic(text)
    else:
        prof = _heuristic(text)
    # keep only skills we can actually detect in text; the rest become plain keywords
    must = _dedupe(skills.canonicalise(s) for s in prof.get("must_have_skills", []))
    nice = _dedupe(skills.canonicalise(s) for s in prof.get("nice_to_have_skills", []))
    prof["must_have_skills"] = [s for s in must if s in skills.TAXONOMY]
    prof["nice_to_have_skills"] = [s for s in nice if s in skills.TAXONOMY and s not in prof["must_have_skills"]]
    prof["keywords"] = _dedupe(list(prof.get("keywords", [])) + [s for s in must + nice if s not in skills.TAXONOMY])
    prof["jd_text"] = text
    ingest.save_jd_profile(slug, prof)
    return prof


def _dedupe(it):
    seen, out = set(), []
    for x in it:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
