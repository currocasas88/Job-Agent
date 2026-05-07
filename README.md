# 🚀 Executive Job Hunter Agent

An autonomous pipeline that finds, scores, and delivers senior product leadership
job listings to your inbox — on a schedule, with zero manual effort.

Built by [Curro Casas](https://currocasas.com) as part of a personal AI agent portfolio.

---

## What It Does

1. **Fetches** active job listings from Indeed (via RapidAPI) for configured locations
2. **Scores** each listing against a candidate profile using Google Gemini AI
3. **Emails** a curated digest with only high-fit listings highlighted, plus full reports attached

Runs automatically every Monday and Thursday via GitHub Actions.

---

## Architecture

```
GitHub Actions (cron)
        │
        ▼
  fetch_jobs()          ← Indeed API via RapidAPI
        │
        ▼
  normalize_job()       ← Consistent schema
        │
        ▼
  evaluate_jobs()       ← Gemini 2.0 Flash (free tier)
        │
        ▼
  generate_report()     ← raw_discovery.txt + scored_results.txt
        │
        ▼
  send_email()          ← Gmail SMTP with HTML digest + attachments
```

---

## APIs Used

| API | Purpose | Cost |
|-----|---------|------|
| [Indeed API via RapidAPI](https://rapidapi.com/indeed/api/indeed12) | Job listings | Free tier (~100 req/month) |
| [Google Gemini 2.0 Flash](https://ai.google.dev/) | AI scoring | Free tier |
| Gmail SMTP | Email delivery | Free |

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/currocasas88/job-agent.git
cd job-agent
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your actual keys
```

### 3. Customize your search

Edit the config block at the top of `main.py`:

```python
SEARCH_CONFIG = [
    {"location": "New York, United States", "region_code": "US"},
    {"location": "Spain", "region_code": "ES"},
]

TARGET_TITLES = [
    "VP Product Management",
    "Head of Product",
    # Add or remove titles here
]

CANDIDATE_PROFILE = """
Your background, strengths, and target roles here.
Used by the AI to score listing fit.
"""

SCORE_THRESHOLD = 7.5  # Minimum score to appear in email highlights
```

### 4. Run locally

```bash
python main.py
```

### 5. Deploy to GitHub Actions

Add the following secrets in your repo under **Settings → Secrets → Actions**:

| Secret | Value |
|--------|-------|
| `RAPIDAPI_KEY` | Your RapidAPI key |
| `GEMINI_API_KEY` | Your Google AI Studio key |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://support.google.com/accounts/answer/185833) |
| `GMAIL_ADDRESS` | Your Gmail address |

Push `.github/workflows/job_agent.yml` — the schedule activates automatically.

---

## Output

**Email digest** — HTML email with scored listings above the threshold:

```
[9.2/10] VP Product Management @ Stripe
  Rationale: Strong AI platform focus, NYC-based, scope matches senior background.
  View Listing →
```

**Attached reports:**
- `raw_discovery.txt` — every listing fetched, by location
- `scored_results.txt` — all AI scores + system diagnostics

Reports are also saved as GitHub Actions artifacts for 7 days.

---

## Project Structure

```
job-agent/
├── main.py                          # Core agent logic
├── requirements.txt
├── .env.example                     # Credential template
├── .gitignore
└── .github/
    └── workflows/
        └── job_agent.yml            # GitHub Actions schedule
```

---

## Related Projects

- [House Hunting Agent](https://github.com/currocasas88/house-hunting) — Same pattern, applied to real estate
- [Portfolio](https://currocasas.com)

---

## License

MIT — fork freely, adapt to your own job search.
