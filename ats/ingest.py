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


def _grid(path):
    """Read a sheet as a list of string rows: real .xlsx, legacy .xls (via LibreOffice), or TSV/CSV text."""
    path = Path(path)
    head = path.read_bytes()[:8]
    if head[:2] == b"PK" or head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        if head[:2] != b"PK":
            import subprocess, tempfile
            with tempfile.TemporaryDirectory() as td:
                subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", td, str(path)],
                               capture_output=True, timeout=120)
                out = Path(td) / (path.stem + ".xlsx")
                if not out.exists():
                    raise ValueError("could not read this .xls file; save it as .xlsx or CSV")
                return _grid(out)
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        return [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
    text = path.read_text("utf-8-sig", "ignore")
    delim = "\t" if text.count("\t") >= text.count(",") else ","
    return [[c.strip() for c in r] for r in csv.reader(text.splitlines(), delimiter=delim)]


def parse_sheet(path):
    """Applicant sheet. The header row is the first one mentioning a roll number (portal exports put a title above it)."""
    grid = _grid(path)
    hi = next((i for i, r in enumerate(grid) if any("roll" in c.lower() for c in r)), None)
    if hi is None:
        raise ValueError("sheet has no 'Roll Number' column")
    title = next((c for r in grid[:hi] for c in r if c), "")
    header = [h.strip() for h in grid[hi]]
    rows = []
    for r in grid[hi + 1:]:
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
            "spec": d.get("Sepecialization Name", d.get("Specialization Name", "")), "cgpa": cgpa,
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
    sheet = next((d / f"applicants{e}" for e in (".tsv", ".xlsx", ".xls", ".csv") if (d / f"applicants{e}").exists()), None)
    if sheet:
        title, rows = parse_sheet(sheet)
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
