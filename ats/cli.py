"""Command line: python -m ats <profile|rubric|rank|serve|companies> ..."""
import argparse
import sys

from . import export, features, ingest, jd, rubric, scoring


def _prepare(slug, with_rubric=True):
    comp = ingest.load_company(slug)
    prof = jd.build_profile(slug)
    cached = rubric.load_cached(slug, comp["candidates"], prof) if with_rubric else {}
    feats, _ = features.compute(comp["candidates"], prof, llm_results=cached)
    return comp, prof, feats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ats")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("companies")
    for n in ("profile", "rubric", "rank"):
        p = sub.add_parser(n)
        p.add_argument("slug")
        if n == "profile":
            p.add_argument("--force", action="store_true")
        if n == "rubric":
            p.add_argument("--top", type=int, help="only the top N by local score")
        if n == "rank":
            p.add_argument("--out", default=None)
            p.add_argument("--top", type=int, default=15)
            p.add_argument("--min-cgpa", type=float)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)

    if a.cmd == "companies":
        print("\n".join(ingest.list_companies()))
    elif a.cmd == "profile":
        prof = jd.build_profile(a.slug, force=a.force)
        for k in ("role_title", "must_have_skills", "nice_to_have_skills", "preferred_programs", "keywords"):
            print(f"{k}: {prof.get(k)}")
        for c in prof["competencies"]:
            print(f"  [{c['weight']}] {c['name']}: {c['description']}")
    elif a.cmd == "rubric":
        comp, prof, feats = _prepare(a.slug, with_rubric=False)
        rolls = None
        if a.top:
            rows = scoring.rank(comp["candidates"], feats)
            rolls = {r["roll"] for r in rows[:a.top]}
        res, errs = rubric.run(a.slug, comp["candidates"], prof, rolls,
                               progress=lambda d, t: print(f"\r{d}/{t}", end="", file=sys.stderr, flush=True))
        print(f"\nscored {len(res)}, errors {len(errs)}")
        for roll, e in errs[:10]:
            print("  ", roll, e)
    elif a.cmd == "rank":
        comp, prof, feats = _prepare(a.slug)
        rows = scoring.rank(comp["candidates"], feats, flt={"min_cgpa": a.min_cgpa})
        for r in rows[:a.top]:
            print(f"{r['rank']:>3} {r['score']:>6.2f}  {r['roll']:<10} {r['name'][:28]:<28} {r['dept'][:24]:<24} {r['cgpa']}")
        if a.out:
            open(a.out, "wb").write(export.to_xlsx(rows, a.slug, scoring.DEFAULT_WEIGHTS, scoring.DEFAULT_ENABLED))
            print("wrote", a.out)
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("ats.app:app", host=a.host, port=a.port)
    return 0
