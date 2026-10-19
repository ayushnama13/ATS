"""FastAPI app: recruiter ranking, candidate check, JD profile editing, xlsx export."""
import json
import os
import re
import shutil
import zipfile
import threading
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, export, extract, features, ingest, jd, llm, rubric, scoring

app = FastAPI(title="ATS Scanner")
STATIC = Path(__file__).parent / "static"
PRESETS = config.DATA / "presets.json"

_lock = threading.RLock()
_state = {}   # slug -> {comp, prof, ctx, feats}
_jobs = {}    # job id -> progress dict


def _company(slug):
    with _lock:
        if slug not in _state:
            try:
                comp = ingest.load_company(slug)
            except FileNotFoundError:
                raise HTTPException(404, f"unknown company {slug}")
            prof = jd.build_profile(slug)
            feats, ctx = features.compute(comp["candidates"], prof, embeddings="cached",
                                          llm_results=rubric.load_cached(slug, comp["candidates"], prof))
            _state[slug] = {"comp": comp, "prof": prof, "ctx": ctx, "feats": feats}
            if ctx.sem_sims is None and llm.available() and not os.environ.get("ATS_NO_WARM"):
                _warm_embeddings(slug)
        return _state[slug]


def _warm_embeddings(slug):
    """Fetch missing embeddings in the background, then rebuild the cohort context."""
    key = "embed:" + slug
    if key in _jobs and _jobs[key]["state"] == "running":
        return
    _jobs[key] = {"done": 0, "total": 0, "state": "running"}

    def run():
        try:
            st = _state[slug]
            docs = [features.jd_embedding_text(st["prof"])] + [c["text"][:8000] or " " for c in st["comp"]["candidates"]]
            llm.embed(docs, progress=lambda d, t: _jobs[key].update(done=d, total=t))
            with _lock:
                _state.pop(slug, None)
                _company(slug)
            _jobs[key]["state"] = "done"
        except Exception as e:
            _jobs[key].update(state="failed", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()


def _refresh_rubric(slug):
    """Re-attach cached rubric results to features (cheap; keeps the cohort context)."""
    with _lock:
        st = _company(slug)
        cached = rubric.load_cached(slug, st["comp"]["candidates"], st["prof"])
        st["feats"], _ = features.compute(st["comp"]["candidates"], st["prof"], llm_results=cached, ctx=st["ctx"])


def _public_profile(prof):
    return {k: v for k, v in prof.items() if k != "jd_text"}


def _load_presets():
    return json.loads(PRESETS.read_text()) if PRESETS.exists() else {}


@app.get("/api/companies")
def companies():
    out = []
    for slug in ingest.list_companies():
        st = _company(slug)
        n = len(st["comp"]["candidates"])
        out.append({"slug": slug, "title": st["comp"]["title"], "role": st["prof"].get("role_title", ""),
                    "candidates": n, "scored": sum(1 for f in st["feats"] if f["features"]["llm"] is not None)})
    return out


@app.get("/api/companies/{slug}")
def company(slug: str):
    st = _company(slug)
    cs = st["comp"]["candidates"]
    return {
        "slug": slug, "title": st["comp"]["title"], "profile": _public_profile(st["prof"]),
        "factors": features.FACTORS, "default_weights": scoring.DEFAULT_WEIGHTS,
        "programs": sorted({c["program"] for c in cs if c["program"]}),
        "departments": sorted({c["dept"] for c in cs if c["dept"]}),
        "unreadable": sum(1 for c in cs if c["words"] < 50),
        "presets": _load_presets().get(slug, {}),
        "semantic": st["ctx"].sem_sims is not None,
        "embed_job": _jobs.get("embed:" + slug),
    }


def _rank(slug, body):
    st = _company(slug)
    return scoring.rank(st["comp"]["candidates"], st["feats"], body.get("weights"), body.get("enabled"),
                        body.get("filters"))


@app.post("/api/companies/{slug}/rank")
def rank(slug: str, body: dict = Body(default={})):
    rows = _rank(slug, body)
    limit = int(body.get("limit", 1000))
    return {"total": len(rows), "rows": [
        {"rank": r["rank"], "roll": r["roll"], "name": r["name"], "program": r["program"], "dept": r["dept"],
         "spec": r["spec"], "cgpa": r["cgpa"], "placed": r["placed"], "score": r["score"],
         "points": {f: p["points"] for f, p in r["parts"].items()},
         "summary": (r["detail"].get("rubric") or {}).get("summary", "")} for r in rows[:limit]]}


@app.get("/api/companies/{slug}/candidate/{roll}")
def candidate(slug: str, roll: str, weights: str = None):
    st = _company(slug)
    roll = roll.upper()
    w = json.loads(weights) if weights else None
    rows = scoring.rank(st["comp"]["candidates"], st["feats"], w)
    row = next((r for r in rows if r["roll"] == roll), None)
    if not row:
        raise HTTPException(404, "candidate not found")
    c = next(c for c in st["comp"]["candidates"] if c["roll"] == roll)
    return {**row, "total": len(rows), "percentile": round(100 * (1 - (row["rank"] - 1) / len(rows)), 1),
            "email": c["email"], "words": c["words"], "pages": c["pages"], "ocr": c["ocr"],
            "resume_file": c["resume_file"], "features": next(f for f in st["feats"] if f["roll"] == roll)["features"]}


@app.get("/api/companies/{slug}/resume/{roll}")
def resume(slug: str, roll: str):
    st = _company(slug)
    c = next((c for c in st["comp"]["candidates"] if c["roll"] == roll.upper()), None)
    if not c or not c["resume_file"]:
        raise HTTPException(404, "no resume file")
    return FileResponse(ingest.company_dir(slug) / "resumes" / c["resume_file"])


@app.put("/api/companies/{slug}/profile")
def update_profile(slug: str, body: dict = Body(...)):
    """Edit skills / competencies / preferences; recomputes features (rubric re-scores only if competencies changed)."""
    with _lock:
        st = _company(slug)
        prof = {**st["prof"]}
        for k in ("must_have_skills", "nice_to_have_skills", "keywords", "preferred_programs",
                  "preferred_departments", "signals", "competencies", "min_cgpa", "summary"):
            if k in body:
                prof[k] = body[k]
        ingest.save_jd_profile(slug, prof)
        _state.pop(slug, None)
    return _public_profile(_company(slug)["prof"])


@app.post("/api/companies/{slug}/profile/regenerate")
def regenerate_profile(slug: str):
    with _lock:
        jd.build_profile(slug, force=True)
        _state.pop(slug, None)
    return _public_profile(_company(slug)["prof"])


@app.put("/api/companies/{slug}/presets/{name}")
def save_preset(slug: str, name: str, body: dict = Body(...)):
    p = _load_presets()
    p.setdefault(slug, {})[name] = body
    PRESETS.write_text(json.dumps(p, indent=1))
    return p[slug]


@app.delete("/api/companies/{slug}/presets/{name}")
def delete_preset(slug: str, name: str):
    p = _load_presets()
    p.get(slug, {}).pop(name, None)
    PRESETS.write_text(json.dumps(p, indent=1))
    return p.get(slug, {})


@app.post("/api/companies/{slug}/export")
def export_xlsx(slug: str, body: dict = Body(default={})):
    rows = _rank(slug, body)
    weights = {**scoring.DEFAULT_WEIGHTS, **(body.get("weights") or {})}
    enabled = {**scoring.DEFAULT_ENABLED, **(body.get("enabled") or {})}
    data = export.to_xlsx(rows, slug, weights, enabled)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{slug}_ranking.xlsx"'})


@app.post("/api/companies/{slug}/rubric")
def start_rubric(slug: str, body: dict = Body(default={})):
    """Run the LLM rubric in the background on the top N by current score (or everyone)."""
    if not llm.available():
        raise HTTPException(400, "GEMINI_API_KEY not set")
    st = _company(slug)
    top = body.get("top")
    rolls = None
    if top:
        rolls = {r["roll"] for r in _rank(slug, {**body, "filters": body.get("filters")})[:int(top)]}
    job = str(uuid.uuid4())[:8]
    _jobs[job] = {"done": 0, "total": 0, "state": "running", "errors": []}

    def run():
        def prog(d, t):
            _jobs[job].update(done=d, total=t)
        try:
            _, errs = rubric.run(slug, st["comp"]["candidates"], st["prof"], rolls, prog)
            _jobs[job]["errors"] = errs[:20]
            _refresh_rubric(slug)
            _jobs[job]["state"] = "done"
        except Exception as e:
            _jobs[job].update(state="failed", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return {"job": job}


@app.get("/api/jobs/{job}")
def job_status(job: str):
    if job not in _jobs:
        raise HTTPException(404, "no such job")
    return _jobs[job]


SUGGEST_PROMPT = """You are an ATS and resume coach for a campus candidate applying to: {title} at {company}.
Role needs: {needs}
Must-have skills: {must}. Their resume currently lacks: {missing}.

RESUME:
{resume}

Return ONLY JSON:
{{"priority_actions": [<=5 concrete changes ordered by impact],
 "missing_keywords": [JD terms they could truthfully add, only if supported by their experience],
 "bullet_rewrites": [{{"original": "<exact bullet from resume>", "improved": "<stronger, quantified version; do NOT invent facts>", "why": "<=15 words"}}] (max 3),
 "ats_tips": [<=4 formatting/parsing tips]}}"""


@app.post("/api/check")
async def check(file: UploadFile = File(...), slug: str = Form(...), cgpa: float = Form(None),
                program: str = Form(""), dept: str = Form(""), suggest: bool = Form(True)):
    """Candidate mode: score an uploaded resume against a company's JD and its applicant pool."""
    st = _company(slug)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ingest.RESUME_EXTS:
        raise HTTPException(400, "upload a PDF, DOCX or DOC")
    config.UPLOADS.mkdir(parents=True, exist_ok=True)
    path = config.UPLOADS / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(await file.read())
    try:
        e = extract.extract(path)
    finally:
        path.unlink(missing_ok=True)
    if e["words"] < 30:
        raise HTTPException(422, "could not read text from this file (scanned image? try a text-based PDF)")
    c = {"roll": "UPLOAD", "name": "You", "program": program, "dept": dept, "spec": "", "cgpa": cgpa, "placed": "",
         "text": e["text"], "pages": e["pages"], "words": e["words"]}
    prof, rub = st["prof"], None
    if llm.available():
        try:
            rub = rubric.score_candidate(c, prof, slug)
        except Exception:
            rub = None
    ft = features.candidate_features(c, prof, st["ctx"], None, rub)
    score, parts = scoring.score_one(ft["features"], scoring.DEFAULT_WEIGHTS, scoring.DEFAULT_ENABLED)
    cohort = [r["score"] for r in scoring.rank(st["comp"]["candidates"], st["feats"])]
    better = sum(1 for s in cohort if s > score)
    out = {"score": round(score, 1), "rank_estimate": better + 1, "pool": len(cohort),
           "percentile": round(100 * (1 - better / max(len(cohort), 1)), 1), "words": e["words"], "pages": e["pages"],
           "features": ft["features"], "detail": ft["detail"], "points": {f: p["points"] for f, p in parts.items()},
           "suggestions": None}
    if suggest and llm.available():
        try:
            out["suggestions"] = llm.generate_json(SUGGEST_PROMPT.format(
                title=prof.get("role_title", ""), company=prof.get("company", slug), needs=prof.get("summary", ""),
                must=", ".join(prof.get("must_have_skills", [])), missing=", ".join(ft["detail"]["missing_must"]) or "nothing major",
                resume=e["text"][:9000]))
        except Exception:
            pass
    return out


def _slugify(title):
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "job"
    slug, n = base, 2
    while (config.COMPANIES / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


@app.post("/api/new-job")
async def new_job(title: str = Form(...), jd_text: str = Form(""), jd_file: UploadFile = File(None),
                  resumes: list[UploadFile] = File(...), sheet: UploadFile = File(None)):
    """Recruiter flow: paste/upload a JD plus resumes (files or a zip) and get a rankable job."""
    jd_file = jd_file if jd_file and jd_file.filename else None
    sheet = sheet if sheet and sheet.filename else None
    if not jd_text.strip() and not jd_file:
        raise HTTPException(400, "provide the job description as text or a file")
    slug = _slugify(title)
    d = config.COMPANIES / slug
    rdir = d / "resumes"
    rdir.mkdir(parents=True)
    try:
        (d / "title.txt").write_text(title.strip())
        if jd_file:
            ext = Path(jd_file.filename).suffix.lower()
            if ext not in (".pdf", ".docx", ".doc", ".txt"):
                raise HTTPException(400, "JD must be PDF, DOCX, DOC or TXT")
            (d / f"jd{ext}").write_bytes(await jd_file.read())
        else:
            (d / "jd.txt").write_text(jd_text)
        if sheet:
            tmp = d / ("_sheet" + Path(sheet.filename).suffix.lower())
            tmp.write_bytes(await sheet.read())
            try:
                _, rows = ingest.parse_sheet(tmp)
            except Exception as e:
                raise HTTPException(400, f"applicant sheet: {e}")
            if not rows:
                raise HTTPException(400, "applicant sheet has no rows with a Roll Number")
            tmp.rename(d / ("applicants" + tmp.suffix))
        used = set()

        def put(name, data):
            stem, ext = Path(name).stem, Path(name).suffix.lower()
            if ext not in ingest.RESUME_EXTS:
                return
            out, n = stem, 2
            while out.upper() in used:
                out, n = f"{stem}-{n}", n + 1
            used.add(out.upper())
            (rdir / f"{out}{ext}").write_bytes(data)

        for up in resumes:
            data = await up.read()
            if (up.filename or "").lower().endswith(".zip"):
                tmp = d / "_up.zip"
                tmp.write_bytes(data)
                with zipfile.ZipFile(tmp) as z:
                    for m in z.infolist():
                        if not m.is_dir() and not Path(m.filename).name.startswith((".", "_")):
                            put(Path(m.filename).name, z.read(m))
                tmp.unlink()
            else:
                put(Path(up.filename or "resume.pdf").name, data)
        if not used:
            raise HTTPException(400, "no PDF/DOCX/DOC resumes found in the upload")
        with _lock:
            _state.pop(slug, None)
        _company(slug)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        _state.pop(slug, None)
        raise
    return {"slug": slug, "resumes": len(used)}


@app.post("/api/companies/{slug}/resumes")
async def add_resumes(slug: str, resumes: list[UploadFile] = File(...)):
    """Add individual resumes (or a zip) to an existing job without recreating it."""
    d = ingest.company_dir(slug)
    rdir = d / "resumes"
    rdir.mkdir(exist_ok=True)
    taken = {f.stem.upper() for f in rdir.glob("*")}
    added, skipped = [], []

    def put(name, data):
        stem, ext = Path(name).stem, Path(name).suffix.lower()
        if ext not in ingest.RESUME_EXTS:
            skipped.append(name)
            return
        out, n = stem, 2
        while out.upper() in taken:
            out, n = f"{stem}-{n}", n + 1
        taken.add(out.upper())
        (rdir / f"{out}{ext}").write_bytes(data)
        added.append(out.upper())

    for up in resumes:
        data = await up.read()
        name = Path(up.filename or "resume.pdf").name
        if name.lower().endswith(".zip"):
            tmp = d / "_up.zip"
            tmp.write_bytes(data)
            with zipfile.ZipFile(tmp) as z:
                for m in z.infolist():
                    if not m.is_dir() and not Path(m.filename).name.startswith((".", "_")):
                        put(Path(m.filename).name, z.read(m))
            tmp.unlink()
        else:
            put(name, data)
    if not added:
        raise HTTPException(400, "no PDF/DOCX/DOC resumes in the upload")
    with _lock:
        _state.pop(slug, None)
        st = _company(slug)
    if llm.available():
        _warm_embeddings(slug)   # new resumes need embeddings; semantic is skipped until they exist
    return {"added": added, "skipped": skipped, "total": len(st["comp"]["candidates"])}


@app.delete("/api/companies/{slug}")
def delete_job(slug: str):
    d = ingest.company_dir(slug)
    if not (d / "title.txt").exists():
        raise HTTPException(403, "only jobs created from the UI can be deleted")
    shutil.rmtree(d)
    with _lock:
        _state.pop(slug, None)
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
