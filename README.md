# ATS scanner

Rank applicants against a job description (recruiter view) and let a candidate check their own resume.

## Setup
    uv sync            # or: python -m venv .venv && pip install -e .
    cp .env.example .env   # then put your key after GEMINI_API_KEY=
    python -m ats env      # shows which .env and keys were loaded (masked)
Per-company data lives in `data/companies/<slug>/` (`jd.docx|pdf`, `applicants.tsv`, `resumes/`). It is git-ignored (PII).

## Use
    python -m ats companies
    python -m ats profile accenture          # JD -> editable skills/competencies (jd.json)
    python -m ats rubric accenture           # Gemini rubric review of every resume (cached)
    python -m ats embed accenture            # semantic embeddings (slow on free tier, cached)
    python -m ats rank accenture --out out.xlsx --top 50
    python -m ats serve --port 8765          # dashboard (use .venv/bin/python if `python` is missing)

**Your own JD:** open the dashboard, click **＋ New job**, paste or upload a JD and upload resumes (many files or one ZIP; an applicant sheet is optional). It builds the JD profile, ranks every resume, and starts the AI review of the top 30.

The dashboard has these tabs: new job, rank applicants (every factor can be toggled and weighted, with filters, presets, Excel export), check a resume, and edit the JD profile.

## Scoring
Weighted mean over enabled factors; a factor that does not apply to a candidate (e.g. no CGPA) drops out rather than counting as zero.
Factors: must/nice skills, JD keywords, TF-IDF, semantic, experience, projects, quantified impact, leadership, CGPA, program fit, branch fit, ATS format, AI rubric.

## Quotas (Gemini free tier)
Generation is limited to ~13 requests/min (resumes are batched 3 per request); embeddings are paced by tokens/min. Everything is cached in `data/cache.sqlite`, so reruns are cheap. Set `ATS_NO_WARM=1` to stop the server warming embeddings in the background.

## Tests
    python -m pytest tests

## Configuration (.env)
`.env` is read from the project root (or the current directory). Real environment variables take precedence over the file.
It supports `export KEY=value`, quotes, inline `# comments`, and CRLF. Placeholder values are treated as "no key".
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) sets one key; `GEMINI_API_KEYS=a,b,c` sets several, rotated on rate limits.
See `.env.example` for every option.

## Resume storage

All resumes live once in `data/resumes/<ROLL>/<hash>.<ext>` (content-addressed: identical bytes are never stored twice; a changed resume becomes a new version of that person). Each job's `resumes/` folder holds only relative symlinks to the chosen version. The applicant sheet (xlsx/xls/csv/tsv) records who applied to which job.

- **Add resumes → From database** links people already stored to the current job; **Upload files** adds new ones.
- The candidate drawer has a version picker when a person has several resumes.
- `python -m ats migrate` moves any legacy real files into the central store and replaces them with symlinks.
