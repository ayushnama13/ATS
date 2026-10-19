"""Paths and environment settings."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COMPANIES = DATA / "companies"
CACHE_DB = DATA / "cache.sqlite"
UPLOADS = DATA / "uploads"
OUT = ROOT / "out"


_PLACEHOLDERS = {"", "your-key-here", "your_api_key", "changeme", "xxx"}


def parse_dotenv(text):
    """Parse .env text: `KEY=value`, `export KEY=value`, quotes, inline `# comments`, BOM and CRLF."""
    out = {}
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v and v[0] in "\"'" and v.count(v[0]) >= 2:
            v = v[1:v.index(v[0], 1)]            # quoted: take what is inside the quotes
        else:
            v = v.split(" #", 1)[0].strip()      # unquoted: drop a trailing comment
        out[k.strip()] = v
    return out


def load_dotenv(path=None):
    """Load .env into os.environ. Real environment variables win, except empty ones. Returns the file used."""
    candidates = [Path(path)] if path else [ROOT / ".env", Path.cwd() / ".env"]
    for f in candidates:
        if f.is_file():
            for k, v in parse_dotenv(f.read_text("utf-8", "ignore")).items():
                if not os.environ.get(k):
                    os.environ[k] = v
            return f
    return None


def _split_keys(*vals):
    keys = []
    for v in vals:
        for k in (v or "").replace(",", " ").split():
            if k.lower() not in _PLACEHOLDERS and k not in keys:
                keys.append(k)
    return keys


ENV_FILE = load_dotenv()

# One key, or several (GEMINI_API_KEYS=a,b,c) which are rotated when one hits its rate limit.
GEMINI_API_KEYS = _split_keys(os.environ.get("GEMINI_API_KEYS"), os.environ.get("GEMINI_API_KEY"),
                              os.environ.get("GOOGLE_API_KEY"))
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""


def _env(*names, default=""):
    return next((os.environ[n] for n in names if os.environ.get(n)), default)


GEMINI_MODEL = _env("ATS_MODEL", "GEMINI_MODEL", default="gemini-3.1-flash-lite")
GEMINI_EMBED_MODEL = _env("ATS_EMBED_MODEL", "GEMINI_EMBED_MODEL", default="gemini-embedding-001")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
LLM_CONCURRENCY = int(os.environ.get("ATS_LLM_CONCURRENCY", "4"))
EMBED_TPM = int(os.environ.get("ATS_EMBED_TPM", "22000"))  # est. tokens/min, free tier is ~30k
EMBED_CHARS = 3500
LLM_RPM = int(os.environ.get("ATS_LLM_RPM", "13")) * max(1, len(GEMINI_API_KEYS))  # 15/min per model per key on the free tier
