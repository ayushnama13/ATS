"""Split a resume into sections and compute simple structural/format signals."""
import re

HEADINGS = {
    "education": r"education|academic|scholastic",
    "experience": r"experience|internship|work history|employment",
    "projects": r"projects?|research",
    "skills": r"skills|technical skills|tools|technologies|courses",
    "positions": r"position|responsibilit|leadership|extra[\s-]?curricular|co-?curricular|activities|clubs",
    "achievements": r"achievement|award|honou?rs|accomplishment|scholastic",
}
_HEAD_RE = {k: re.compile(r"^\W*(?:\w+\s+){0,2}(?:" + v + r")\b.{0,40}$", re.I) for k, v in HEADINGS.items()}
_BULLET = re.compile(r"^\s*[•\-•●▪\*▪●]\s+")
_QUANT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x\b|k\b|\+|lakh|crore|users|customers|ms\b|hours?|days?)|\b\d{2,}\b|\$\s?\d|₹\s?\d")
_ACTION = re.compile(r"^\W*(built|developed|designed|led|launched|implemented|created|achieved|improved|reduced|increased|"
                     r"optimi[sz]ed|analy[sz]ed|managed|delivered|deployed|automated|drove|established|won|secured|"
                     r"organi[sz]ed|conducted|trained|engineered|authored|spearheaded|shipped|owned|scaled)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\d\s\-]{8,}\d)")
_LINK = re.compile(r"(?:github\.com|linkedin\.com|kaggle\.com|behance\.net|portfolio|https?://)", re.I)


def split_sections(text):
    """Return {section: text}. Lines that look like headings start a new section."""
    secs, cur, buf = {}, "header", []
    for line in text.splitlines():
        s = line.strip()
        hit = None
        if s and len(s) < 60 and not _BULLET.match(s):
            for k, rx in _HEAD_RE.items():
                if rx.match(s):
                    hit = k
                    break
        if hit:
            secs[cur] = secs.get(cur, "") + "\n".join(buf) + "\n"
            cur, buf = hit, []
        else:
            buf.append(line)
    secs[cur] = secs.get(cur, "") + "\n".join(buf) + "\n"
    return secs


def analyse(text, pages=1, words=None):
    """Structural signals used for the ATS-format and impact features."""
    secs = split_sections(text)
    lines = [l for l in text.splitlines() if l.strip()]
    bullets = [l for l in lines if _BULLET.match(l)]
    quant = [l for l in bullets if _QUANT.search(l)]
    action = [l for l in bullets if _ACTION.match(_BULLET.sub("", l))]
    return {
        "sections": [k for k, v in secs.items() if k != "header" and len(v.split()) > 5],
        "skills_text": secs.get("skills", ""),
        "bullets": len(bullets),
        "quantified_bullets": len(quant),
        "action_bullets": len(action),
        "has_email": bool(_EMAIL.search(text)),
        "has_phone": bool(_PHONE.search(text)),
        "has_links": bool(_LINK.search(text)),
        "pages": pages,
        "words": words if words is not None else len(text.split()),
    }
