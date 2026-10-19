from ats import ingest, scoring, sections, skills


def test_find_skills_aliases_and_word_boundaries():
    hits = skills.find_skills("Built models in Python using scikit-learn and PostgreSQL")
    assert "python" in hits
    assert "sql" not in skills.find_skills("pythonic javascripty text")


def test_canonicalise_unknown_returns_none_or_input():
    assert skills.canonicalise("Python") == "python"


def test_split_sections_detects_headings():
    text = "Jane Doe\njane@x.com\nEDUCATION\nIIT Madras BTech 2025 CGPA 9.0 in computer science\nSKILLS\nPython, SQL\n"
    secs = sections.split_sections(text)
    assert "education" in secs and "skills" in secs
    assert "Python" in secs["skills"]


def test_analyse_counts_quantified_bullets():
    text = "• Reduced latency by 40% across 3 services\n• Wrote docs\njane@x.com\n"
    a = sections.analyse(text)
    assert a["bullets"] == 2 and a["quantified_bullets"] == 1 and a["has_email"]


def test_parse_sheet(tmp_path):
    p = tmp_path / "a.tsv"
    p.write_text("Title row\nStudent Name\tRoll Number\tCGPA\tProgram Name\n"
                 "  john  smith \tbs22b001\t8.5\tDUAL\n"
                 "\t\t\t\n"
                 "No Cgpa\tbs22b002\tn/a\tM.S\n")
    title, rows = ingest.parse_sheet(p)
    assert title == "Title row" and len(rows) == 2
    assert rows[0]["roll"] == "BS22B001" and rows[0]["name"] == "John Smith" and rows[0]["cgpa"] == 8.5
    assert rows[1]["cgpa"] is None


def test_score_one_drops_none_and_disabled():
    w = {"must_skill": 10, "cgpa": 10, "llm": 10}
    feats = {"must_skill": 1.0, "cgpa": 0.0, "llm": None}
    s, parts = scoring.score_one(feats, w, {"llm": True})
    assert round(s) == 50 and parts["llm"]["weight"] == 0
    s, _ = scoring.score_one(feats, w, {"cgpa": False})
    assert round(s) == 100


def _cand(roll, cgpa, **kw):
    return {"roll": roll, "name": roll, "program": "M.S", "dept": "CS", "spec": "", "cgpa": cgpa,
            "words": 300, **kw}


def test_rank_orders_and_filters():
    cs = [_cand("A", 6.0), _cand("B", 9.0), _cand("C", None)]
    fs = [{"features": {"must_skill": v}, "detail": {}} for v in (1.0, 0.5, 0.8)]
    rows = scoring.rank(cs, fs, weights={"must_skill": 1})
    assert [r["roll"] for r in rows] == ["A", "C", "B"] and rows[0]["rank"] == 1
    rows = scoring.rank(cs, fs, flt={"min_cgpa": 7})
    assert [r["roll"] for r in rows] == ["B"]


def test_parse_sheet_xlsx_with_title_row(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Export title"])
    ws.append(["Student Name", "Roll Number", "CGPA"])
    ws.append(["ann lee", "cs22b001", 8.1])
    p = tmp_path / "s.xlsx"
    wb.save(p)
    title, rows = ingest.parse_sheet(p)
    assert title == "Export title" and rows[0]["roll"] == "CS22B001" and rows[0]["cgpa"] == 8.1


def test_tfidf_context_survives_tiny_pool():
    from ats.features import Context
    c = {"text": "python sql backend engineer", "roll": "A", "words": 4}
    ctx = Context([c], {"jd_text": "python sql", "keywords": []}, embeddings=None)
    assert len(ctx.tf_sims) == 1


def test_search_keeps_global_rank():
    cs = [_cand("A", 6.0, name="Zed"), _cand("B", 9.0, name="Ayush"), _cand("C", 7.0, name="Cy")]
    fs = [{"features": {"must_skill": v}, "detail": {}} for v in (1.0, 0.5, 0.8)]
    rows = scoring.rank(cs, fs, weights={"must_skill": 1}, flt={"search": "ayush"})
    assert [(r["roll"], r["rank"]) for r in rows] == [("B", 3)]


def test_parse_dotenv_formats():
    from ats.config import parse_dotenv
    t = '﻿# c\r\nA=1\r\nexport B="two words" # note\r\nC=\'x\'\r\nD=val # trailing\r\nE=\r\nnoequals\r\n'
    assert parse_dotenv(t) == {"A": "1", "B": "two words", "C": "x", "D": "val", "E": ""}


def test_split_keys_ignores_placeholders_and_dupes():
    from ats.config import _split_keys
    assert _split_keys("a, b", "your-key-here", "a", None) == ["a", "b"]


def test_load_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    from ats.config import load_dotenv
    f = tmp_path / ".env"
    f.write_text("ZZ_TEST_A=file\nZZ_TEST_B=file\n")
    monkeypatch.setenv("ZZ_TEST_A", "real")
    monkeypatch.delenv("ZZ_TEST_B", raising=False)
    assert load_dotenv(f) == f
    import os
    assert os.environ["ZZ_TEST_A"] == "real" and os.environ["ZZ_TEST_B"] == "file"
    monkeypatch.delenv("ZZ_TEST_B", raising=False)
