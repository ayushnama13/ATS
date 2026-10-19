"""Central resume store.

Every distinct resume file is kept exactly once, content-addressed, under
    data/resumes/<ROLL>/<sha1[:12]>.<ext>
(a person can have several versions). A job's `resumes/` folder holds only symlinks,
    data/companies/<slug>/resumes/<ROLL>.<ext>  ->  ../../../resumes/<ROLL>/<hash>.<ext>
pointing at the version that job uses. Who applied to which job is tracked by each job's applicant sheet.
"""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from . import config

STORE = config.DATA / "resumes"
EXTS = (".pdf", ".docx", ".doc")
_ROLL = re.compile(r"[A-Za-z]{2,3}\d{2}[A-Za-z]\d{3,4}")
_lock = threading.RLock()


def digest(data):
    return hashlib.sha1(data).hexdigest()[:12]


def roll_from_name(filename, data=None):
    """Person id from a file name: an IITM-style roll number if one is in the name, else the cleaned stem.
    Stems with no digits ("resume", "cv_final") would merge unrelated people, so they get a content suffix."""
    stem = Path(filename).stem
    m = _ROLL.search(stem)
    if m:
        return m.group(0).upper()
    clean = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").upper() or "RESUME"
    if not re.search(r"\d", clean) and data is not None:
        clean = f"{clean}_{digest(data)[:6].upper()}"
    return clean[:60]


def person_dir(roll):
    return STORE / roll


def _meta(roll):
    f = person_dir(roll) / "meta.json"
    return json.loads(f.read_text()) if f.exists() else {"files": {}}


def _save_meta(roll, meta):
    (person_dir(roll) / "meta.json").write_text(json.dumps(meta, indent=1))


def add(data, filename, roll=None):
    """Store one resume. Returns (roll, hash, ext, created). Identical bytes are never stored twice."""
    ext = Path(filename).suffix.lower()
    if ext not in EXTS:
        raise ValueError(f"unsupported file type: {filename}")
    roll = (roll or roll_from_name(filename, data)).upper()
    h = digest(data)
    with _lock:
        d = person_dir(roll)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"{h}{ext}"
        created = not dest.exists()
        if created:
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
        meta = _meta(roll)
        info = meta["files"].setdefault(dest.name, {"names": []})
        if filename not in info["names"]:
            info["names"].append(filename)
        _save_meta(roll, meta)
    return roll, h, ext, created


def versions(roll):
    """Stored versions of a person's resume, newest first."""
    d = person_dir(roll)
    if not d.is_dir():
        return []
    meta = _meta(roll)["files"]
    fs = [f for f in d.iterdir() if f.suffix.lower() in EXTS]
    fs.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"hash": f.stem, "ext": f.suffix, "file": f.name, "size": f.stat().st_size,
             "names": meta.get(f.name, {}).get("names", [])} for f in fs]


def resolve(roll, h=None):
    vs = versions(roll)
    if h:
        vs = [v for v in vs if v["hash"] == h]
    return person_dir(roll) / vs[0]["file"] if vs else None


def link(slug, roll, h=None):
    """Point a job's resumes/<ROLL>.<ext> at a stored version (default: the newest). Replaces any older link."""
    target = resolve(roll, h)
    if not target:
        raise FileNotFoundError(f"no stored resume for {roll}")
    rdir = config.COMPANIES / slug / "resumes"
    rdir.mkdir(parents=True, exist_ok=True)
    for old in rdir.glob("*"):
        if old.stem.upper() == roll.upper():
            old.unlink()
    lnk = rdir / f"{roll}{target.suffix}"
    lnk.symlink_to(os.path.relpath(target, rdir))
    return lnk


def linked_hash(slug, roll):
    rdir = config.COMPANIES / slug / "resumes"
    for f in rdir.glob("*"):
        if f.stem.upper() == roll.upper() and f.is_symlink():
            return Path(os.readlink(f)).stem
    return None


def migrate(progress=None):
    """Move real files in every job's resumes/ into the store and leave symlinks. Safe to re-run."""
    moved = dedup = linked = 0
    for cdir in sorted(p for p in config.COMPANIES.iterdir() if p.is_dir()):
        rdir = cdir / "resumes"
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.iterdir()):
            if f.is_symlink() or f.suffix.lower() not in EXTS:
                linked += f.is_symlink()
                continue
            data = f.read_bytes()
            roll, h, _, created = add(data, f.name)
            moved += created
            dedup += not created
            link(cdir.name, roll, h)   # link() replaces the real file of the same name with a symlink
        if progress:
            progress(cdir.name)
    return {"stored": moved, "deduplicated": dedup, "already_linked": linked}
