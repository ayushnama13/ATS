"""Load a company folder: applicant sheet (TSV in .xls clothing) + resumes + JD."""
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .extract import extract

RESUME_EXTS = (".pdf", ".docx", ".doc")


def company_dir(slug):
    d = config.COMPANIES / slug
    if not d.is_dir():
        raise FileNotFoundError(f"no such company: {slug}")
    return d


def list_companies():
    if not config.COMPANIES.exists():
        return []
    return sorted(p.name for p in config.COMPANIES.iterdir() if p.is_dir())


def parse_sheet(path):
    """Placement-portal export: row 1 is a title, row 2 the header, then one row per student."""
    lines = Path(path).read_text("utf-8", "ignore").splitlines()
    title = lines[0].strip() if lines else ""
    reader = csv.reader(lines[1:], delimiter="\t")
    header = [h.strip() for h in next(reader)]
    rows = []
    for r in reader:
        if not any(c.strip() for c in r):
            continue
        d = dict(zip(header, (c.strip() for c in r)))
        roll = d.get("Roll Number", "").upper()
        if not roll:
            continue
        try:
            cgpa = float(d.get("CGPA", ""))
        except ValueError:
            cgpa = None
        rows.append({
            "roll": roll, "name": " ".join(d.get("Student Name", "").split()).title(),
            "email": d.get("Email Address", ""), "gender": d.get("Gender", ""),
            "program": d.get("Program Name", ""), "dept": d.get("Department Name", ""),
            "spec": d.get("Sepecialization Name", ""), "cgpa": cgpa,
            "status": d.get("Status", ""), "placed": d.get("Placed Status", ""),
        })
    return title, rows


def _title(d):
    t = d / "title.txt"
    return t.read_text().strip() if t.exists() else d.name


def rows_from_resumes(rdir):
    """No applicant sheet: one candidate per resume file, named after the file."""
    rows = []
    for f in sorted(Path(rdir).glob("*")):
        if f.suffix.lower() not in RESUME_EXTS:
            continue
        name = " ".join(re.sub(r"[_\-.]+|\bresume\b|\bcv\b", " ", f.stem, flags=re.I).split()).title()
        rows.append({"roll": f.stem.upper(), "name": name or f.stem, "email": "", "gender": "", "program": "",
                     "dept": "", "spec": "", "cgpa": None, "status": "", "placed": "", "auto_name": True})
    return rows


_NOT_NAME = {"education", "resume", "curriculum", "vitae", "cv", "summary", "experience", "skills", "projects",
             "objective", "profile", "contact", "achievements", "of", "and"}


def _name_from_text(text):
    """Resumes put the candidate's name on the first line; accept it only if it looks like a name."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.split(r"\s[|\-–•·,]\s|\s{3,}|\d|@", line)[0]
        words = re.sub(r"^name\b[:\s]*", "", line.strip(), flags=re.I).split()
        if 2 <= len(words) <= 4 and not {w.lower() for w in words} & _NOT_NAME and all(re.fullmatch(r"[A-Za-z.'-]+", w) for w in words):
            return " ".join(words).title()
        return None
    return None


def find_resume(resume_dir, roll):
    for f in Path(resume_dir).glob("*"):
        if f.suffix.lower() in RESUME_EXTS and f.stem.upper() == roll:
            return f
    return None


def load_company(slug, with_text=True, ocr=True):
    d = company_dir(slug)
    rdir = d / "resumes"
    if (d / "applicants.tsv").exists():
        title, rows = parse_sheet(d / "applicants.tsv")
    else:
        title, rows = _title(d), rows_from_resumes(rdir)
    seen, cands = set(), []
    for r in rows:
        if r["roll"] in seen:
            continue
        seen.add(r["roll"])
        r["resume_file"] = None
        r["text"], r["pages"], r["words"], r["ocr"], r["error"] = "", 0, 0, False, None
        cands.append(r)

    def work(c):
        f = find_resume(rdir, c["roll"])
        if not f:
            c["error"] = "resume file missing"
            return
        c["resume_file"] = f.name
        if with_text:
            e = extract(f, allow_ocr=ocr)
            c.update(text=e["text"], pages=e["pages"], words=e["words"], ocr=e.get("ocr", False),
                     error=e.get("error"))
            if c.get("auto_name"):
                c["name"] = _name_from_text(e["text"]) or c["name"]

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, cands))
    return {"slug": slug, "title": title, "candidates": cands}


def jd_file(slug):
    d = company_dir(slug)
    for n in ("jd.docx", "jd.pdf", "jd.doc", "jd.txt"):
        if (d / n).exists():
            return d / n
    return None


def jd_text(slug):
    f = jd_file(slug)
    if not f:
        raise FileNotFoundError(f"no JD file for {slug}")
    if f.suffix == ".txt":
        return f.read_text("utf-8", "ignore")
    return extract(f, allow_ocr=False)["text"]


def load_jd_profile(slug):
    p = company_dir(slug) / "jd.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_jd_profile(slug, profile):
    (company_dir(slug) / "jd.json").write_text(json.dumps(profile, indent=2))
