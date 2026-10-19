# ATS scanner

Rank applicants against a job description (recruiter view) and let a candidate check their own resume.

## Setup
    uv sync            # or: python -m venv .venv && pip install -e .
    cp .env.example .env   # add GEMINI_API_KEY
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
