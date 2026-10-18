"""Tiny sqlite key/value cache for extracted text, LLM output and embeddings."""
import json
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn = None


def _db():
    global _conn
    if _conn is None:
        config.DATA.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.CACHE_DB, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, val TEXT NOT NULL)")
    return _conn


def get(key):
    with _lock:
        row = _db().execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(key, value):
    with _lock:
        _db().execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
        _db().commit()


def delete_prefix(prefix):
    with _lock:
        _db().execute("DELETE FROM kv WHERE key LIKE ?", (prefix + "%",))
        _db().commit()
