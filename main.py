"""
Executive Job Hunter Agent
--------------------------
Fetches senior product/executive job listings from the Indeed API (via RapidAPI),
scores each listing against a configurable profile using Google Gemini AI,
and delivers a curated digest by email.

Runs on a schedule via GitHub Actions. All credentials are injected as
environment variables — no secrets are ever hardcoded.

Setup:
    1. Copy .env.example to .env for local runs (never commit .env)
    2. Add RAPIDAPI_KEY, GEMINI_API_KEY, GMAIL_APP_PASSWORD, GMAIL_ADDRESS
       as GitHub Actions secrets for automated runs.

Author: Curro Casas (github.com/currocasas88)
"""

import os
import json
import requests
import smtplib
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google import genai

# ---------------------------------------------------------------------------
# CONFIGURATION — edit this block to match your target role and locations
# ---------------------------------------------------------------------------

# Locations to search. Add or remove entries as needed.
SEARCH_CONFIG = [
    {"location": "New York, United States", "region_code": "US"},
    {"location": "Spain",                   "region_code": "ES"},
    # {"location": "Singapore",             "region_code": "SG"},
]

# Job titles to search for. Uses OR logic — any match is included.
TARGET_TITLES = [
    "VP Product Management",
    "Director Product Management",
    "Head of Product",
    "VP Product",
    "Director of Product",
    "Country Manager",
    "Product Executive",
]

# Scoring threshold — only jobs at or above this score are highlighted in email.
SCORE_THRESHOLD = 7.5

# Candidate profile context — used by the AI to score fit.
# Update this to reflect your own background.
CANDIDATE_PROFILE = """
Senior product and technology leader with 12+ years of experience.
Background: Amazon (AI platform, Alexa, 600M+ devices), ADP (DataCloud, global data platform),
Airbus (technical PM), and founder (travel tech startup).
Core strengths: AI/ML product strategy, developer experience, platform scaling,
data-driven growth, agentic systems, cross-functional leadership.
Target: VP / Director / Head of Product roles focused on AI, growth, or developer platforms.
Locations: New York (priority), Spain, Singapore.
"""

# ---------------------------------------------------------------------------
# GLOBALS
# ---------------------------------------------------------------------------

api_status_messages = []  # Collects status messages for the email digest


# ---------------------------------------------------------------------------
# JOB FETCHING — Indeed API via RapidAPI (free tier: ~100 req/month)
# ---------------------------------------------------------------------------

def build_title_query() -> str:
    """Builds a quoted OR query string from TARGET_TITLES."""
    return " OR ".join(f'"{t}"' for t in TARGET_TITLES)


def fetch_jobs(location: str) -> list[dict]:
    """
    Fetches active job listings for a given location using the Indeed API.
    Returns a list of job dicts, or an empty list on error.
    """
    url = "https://indeed12.p.rapidapi.com/jobs/search"
    params = {
        "query": build_title_query(),
        "location": location,
        "page_id": "1",
        "locality": "us",           # Change per region if needed
        "fromage": "7",             # Posted within last 7 days
        "radius": "50",
        "sort": "date",
    }

    headers = {
        "X-RapidAPI-Key":  os.environ["RAPIDAPI_KEY"],
        "X-RapidAPI-Host": "indeed12.p.rapidapi.com",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        # Indeed API returns hits inside "hits" key
        jobs = data.get("hits", [])
        api_status_messages.append(f"✅ Indeed API: {len(jobs)} listings for '{location}'.")
        return jobs
    except requests.exceptions.HTTPError as e:
        api_status_messages.append(f"❌ Indeed API HTTP error ({location}): {e}")
    except requests.exceptions.RequestException as e:
        api_status_messages.append(f"❌ Indeed API request failed ({location}): {e}")
    except (KeyError, ValueError) as e:
        api_status_messages.append(f"❌ Indeed API parse error ({location}): {e}")
    return []


def normalize_job(raw: dict) -> dict:
    """
    Maps raw Indeed API fields to a consistent internal schema.
    Extend this if the API response shape changes.
    """
    return {
        "title":       raw.get("title", "Unknown Title"),
        "company":     raw.get("company", {}).get("name", "Unknown Company"),
        "location":    raw.get("location", "Unknown Location"),
        "url":         f"https://www.indeed.com/viewjob?jk={raw.get('id', '')}" if raw.get("id") else raw.get("url", "#"),
        "description": raw.get("description", "")[:2000],  # Cap to stay within token budget
        "posted":      raw.get("pubDate", ""),
    }


# ---------------------------------------------------------------------------
# AI SCORING — Google Gemini (free tier)
# ---------------------------------------------------------------------------

SCORING_PROMPT_TEMPLATE = """
You are an expert executive recruiter. Score the following job listings for candidate fit.

CANDIDATE PROFILE:
{profile}

SCORING CRITERIA:
- 9-10: Exceptional fit. AI/platform/growth focus, senior IC or leadership scope, target location.
- 7-8:  Strong fit. Most criteria match, minor gaps.
- 5-6:  Partial fit. Relevant domain but wrong seniority, location, or focus.
- 1-4:  Poor fit. Mismatch on multiple dimensions.

Return ONLY a valid JSON array. Each element must have exactly these keys:
  title, company, url, numeric_score (float), rationale (1-2 sentences max)

No markdown. No preamble. No trailing text. Valid JSON only.

JOB LISTINGS:
{jobs_json}
"""


def evaluate_jobs(jobs: list[dict]) -> list[dict]:
    """
    Sends a batch of normalized jobs to Gemini for fit scoring.
    Returns a list of scored job dicts.
    """
    if not jobs:
        return []

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # Trim description to keep prompt within safe token range
    batch = [
        {k: v for k, v in j.items() if k != "description"}
        | {"description": j["description"][:1000]}
        for j in jobs
    ]

    prompt = SCORING_PROMPT_TEMPLATE.format(
        profile=CANDIDATE_PROFILE.strip(),
        jobs_json=json.dumps(batch, ensure_ascii=False, indent=2),
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        scored = json.loads(response.text.strip())
        api_status_messages.append(f"✅ Gemini AI: scored {len(scored)} listings.")
        return scored
    except json.JSONDecodeError as e:
        api_status_messages.append(f"❌ Gemini JSON parse error: {e}")
    except Exception as e:
        api_status_messages.append(f"❌ Gemini error: {e}")
    return []


# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def generate_report(all_raw: dict, all_scored: list[dict]) -> list[str]:
    """
    Writes two plain-text report files:
      - raw_discovery.txt  : all fetched listings by location
      - scored_results.txt : AI scores + diagnostics
    Returns a list of file paths.
    """
    with open("raw_discovery.txt", "w", encoding="utf-8") as f:
        f.write("=== RAW DISCOVERY ===\n")
        for loc, jobs in all_raw.items():
            f.write(f"\nLocation: {loc} ({len(jobs)} listings)\n")
            f.write("-" * 40 + "\n")
            for j in jobs:
                f.write(f"  [{j['posted']}] {j['title']} @ {j['company']} — {j['location']}\n")
                f.write(f"  {j['url']}\n\n")

    sorted_scored = sorted(all_scored, key=lambda x: x.get("numeric_score", 0), reverse=True)

    with open("scored_results.txt", "w", encoding="utf-8") as f:
        f.write("=== SYSTEM DIAGNOSTICS ===\n")
        for msg in api_status_messages:
            f.write(f"  {msg}\n")

        f.write("\n=== AI-SCORED RESULTS ===\n")
        for j in sorted_scored:
            score = j.get("numeric_score", "?")
            f.write(f"\n[{score}/10] {j.get('title')} @ {j.get('company')}\n")
            f.write(f"  Rationale: {j.get('rationale')}\n")
            f.write(f"  URL: {j.get('url')}\n")

    return ["raw_discovery.txt", "scored_results.txt"]


# ---------------------------------------------------------------------------
# EMAIL DELIVERY
# ---------------------------------------------------------------------------

def build_email_html(high_matches: list[dict], all_scored: list[dict]) -> str:
    """Builds a clean HTML email body."""
    lines = ["<html><body style='font-family:sans-serif;max-width:600px;margin:auto;color:#1a1916;'>"]
    lines.append("<h2 style='border-bottom:2px solid #2a5c45;padding-bottom:8px;'>🚀 Executive Job Digest</h2>")

    if high_matches:
        lines.append(f"<p><b>{len(high_matches)} high-fit listing(s)</b> above score {SCORE_THRESHOLD}:</p>")
        for m in sorted(high_matches, key=lambda x: x.get("numeric_score", 0), reverse=True):
            lines.append(f"""
            <div style='border:1px solid #e0dbd2;padding:16px;margin:12px 0;border-radius:4px;'>
              <b style='font-size:16px;'>{m.get('title')}</b><br>
              <span style='color:#6b6860;'>{m.get('company')}</span>
              <span style='float:right;background:#2a5c45;color:#fff;padding:2px 8px;
                           border-radius:12px;font-size:13px;'>{m.get('numeric_score')}/10</span>
              <p style='margin:8px 0;font-size:14px;color:#6b6860;'>{m.get('rationale','')}</p>
              <a href='{m.get('url','#')}' style='color:#2a5c45;font-weight:bold;'>View Listing →</a>
            </div>""")
    else:
        lines.append("<p>No listings exceeded the score threshold this run. See attached reports for full results.</p>")

    lines.append(f"<hr style='margin:24px 0;border-color:#e0dbd2;'>")
    lines.append(f"<p style='font-size:12px;color:#c8c5be;'>")
    lines.append(f"{len(all_scored)} total listings evaluated · Threshold: {SCORE_THRESHOLD}/10<br>")
    lines.append(f"Full details in attached reports.</p>")
    lines.append("</body></html>")
    return "\n".join(lines)


def send_email(html_body: str, attachments: list[str]) -> None:
    """Sends the digest email with report attachments via Gmail SMTP."""
    gmail_address  = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"🚀 Job Digest — {len([a for a in attachments if 'scored' in a])} reports attached"
    msg["From"]    = gmail_address
    msg["To"]      = gmail_address  # Send to self; change if needed
    msg.attach(MIMEText(html_body, "html"))

    for path in attachments:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.send_message(msg)
        api_status_messages.append("✅ Email delivered.")
    except smtplib.SMTPException as e:
        api_status_messages.append(f"❌ Email error: {e}")
        raise  # Re-raise so GitHub Actions marks the run as failed


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    all_raw_jobs: dict[str, list[dict]] = {}
    all_scored:   list[dict] = []

    for config in SEARCH_CONFIG:
        location    = config["location"]
        raw_jobs    = fetch_jobs(location)
        norm_jobs   = [normalize_job(j) for j in raw_jobs]
        all_raw_jobs[location] = norm_jobs

        if norm_jobs:
            scored = evaluate_jobs(norm_jobs)
            all_scored.extend(scored)

    report_files  = generate_report(all_raw_jobs, all_scored)
    high_matches  = [j for j in all_scored if j.get("numeric_score", 0) >= SCORE_THRESHOLD]
    html_body     = build_email_html(high_matches, all_scored)

    send_email(html_body, report_files)
    print("\n".join(api_status_messages))  # Surfaced in GitHub Actions logs


if __name__ == "__main__":
    main()
