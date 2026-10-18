"""Paths and environment settings."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COMPANIES = DATA / "companies"
CACHE_DB = DATA / "cache.sqlite"
UPLOADS = DATA / "uploads"
OUT = ROOT / "out"


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("ATS_MODEL", "gemini-3.1-flash-lite")
GEMINI_EMBED_MODEL = os.environ.get("ATS_EMBED_MODEL", "gemini-embedding-001")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
LLM_CONCURRENCY = int(os.environ.get("ATS_LLM_CONCURRENCY", "4"))
LLM_RPM = int(os.environ.get("ATS_LLM_RPM", "13"))  # free tier allows 15/min per model
