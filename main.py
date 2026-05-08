"""
Executive Job Hunter Agent
--------------------------
Fetches senior product/executive job listings from LinkedIn (via RapidAPI),
scores each against a profile using Google Gemini AI,
and delivers a curated digest by email.

API: linkedin-job-search-api.p.rapidapi.com (~20 req/month free tier)
Schedule: Mon + Thu at noon UTC via GitHub Actions

Author: Curro Casas (github.com/currocasas88)
"""

import os, json, requests, smtplib, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google import genai

# ── CONFIGURATION ──────────────────────────────────────────────────────────

SEARCH_CONFIG = [
    {"location": "New York, United States", "region_code": "US"},
    {"location": "Spain",                   "region_code": "ES"},
]

# Tight title filter — LinkedIn API supports quoted OR queries
TITLE_FILTER = (
    '"VP Product" OR "Director of Product" OR "Head of Product" OR '
    '"VP Product Management" OR "Director Product Management" OR '
    '"Chief Product Officer" OR "Product Executive"'
)

# Only surface jobs above this score in the email highlight
SCORE_THRESHOLD = 7.5

CANDIDATE_PROFILE = """
Senior product and technology leader, 12+ years experience.
Most recent: Sr. Technical Product Leader at Amazon Alexa (AI platform, 600M+ devices, 4.5 years).
Before that: Lead PM at ADP DataCloud (global AI/data platform, 1.1M clients, managed team of 3 PMs).
Also: Airbus technical PM, ITAV travel-tech founder (10k+ users).
Currently: Chief Product & Growth Officer at Arquia (e-commerce, AI agents).

Target roles: VP Product, Director of Product, Head of Product, CPO.
Sectors: AI, SaaS, developer platforms, e-commerce, fintech, marketplace.
Seniority: Must be VP / Director / C-level — NOT senior manager, NOT IC.
Scope: P&L ownership or org leadership preferred.
Locations preferred: New York (top priority), Spain.
Languages: English (fluent), Spanish (native), German (advanced).

Hard disqualifiers: junior roles, non-product roles, staffing agencies posting generic titles,
roles requiring 15+ years when candidate has 12.
"""

log: list[str] = []

# ── FETCHING ───────────────────────────────────────────────────────────────

def fetch_jobs(location: str) -> list[dict]:
    params = {
        "limit": "15",
        "title_filter": TITLE_FILTER,
        "location_filter": location,
        "description_type": "text",
    }
    query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"https://linkedin-job-search-api.p.rapidapi.com/active-jb-7d?{query_string}"
    headers = {
        "X-RapidAPI-Key":  os.environ["RAPIDAPI_KEY"],
        "X-RapidAPI-Host": "linkedin-job-search-api.p.rapidapi.com",
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("data", []) if isinstance(data, dict) else data
        log.append(f"✅ LinkedIn: {len(jobs)} listings for '{location}'")
        return jobs
    except requests.exceptions.HTTPError:
        log.append(f"❌ LinkedIn HTTP {r.status_code} for '{location}': {r.text[:200]}")
    except Exception as e:
        log.append(f"❌ LinkedIn error for '{location}': {e}")
    return []


def normalise(raw: dict) -> dict:
    return {
        "title":    raw.get("title", "Unknown"),
        "company":  raw.get("organization", raw.get("company", "Unknown")),
        "location": raw.get("locations_derived", [raw.get("location", "")])[0]
                    if isinstance(raw.get("locations_derived"), list)
                    else raw.get("location", ""),
        "url":      raw.get("url", raw.get("linkedin_url", "#")),
        "desc":     (raw.get("description_text") or raw.get("description") or "")[:1000],
        "posted":   raw.get("date_posted", raw.get("date", "")),
        "seniority": raw.get("seniority_level", ""),
        "type":     raw.get("employment_type", ""),
    }

# ── SCORING ────────────────────────────────────────────────────────────────

PROMPT = """\
You are an expert executive recruiter scoring job listings for a specific candidate.

CANDIDATE PROFILE:
{profile}

SCORING RUBRIC:
10:  Perfect match — VP/Director/CPO at AI/SaaS/platform company, right location, clear P&L or org scope
8-9: Strong match — senior scope, relevant sector, minor gaps (location, industry)
6-7: Partial match — right seniority but wrong sector, or right sector but unclear seniority
4-5: Weak — staffing agency post, vague JD, or seniority mismatch
1-3: Disqualify — junior, non-product, or completely off-profile

IMPORTANT FILTERS — score 3 or below if any of these apply:
- Title is "Senior Manager" or below
- Posted by a staffing agency with a generic title
- Role is clearly not a product leadership role
- Company is tiny (<10 employees) for a VP-level role

Return ONLY a valid JSON array. Each element must have exactly these keys:
  title (str), company (str), url (str), numeric_score (float 1-10),
  rationale (str, max 2 sentences), flag (str: "APPLY" | "REVIEW" | "SKIP")

No markdown. No preamble. Valid JSON only.

LISTINGS TO SCORE:
{jobs}
"""

def score_jobs(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = PROMPT.format(
        profile=CANDIDATE_PROFILE.strip(),
        jobs=json.dumps(jobs, ensure_ascii=False, indent=2),
    )
    try:
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        scored = json.loads(resp.text.strip())
        log.append(f"✅ Gemini: scored {len(scored)} listings")
        return scored
    except json.JSONDecodeError as e:
        log.append(f"❌ Gemini JSON parse error: {e}")
        log.append(f"   Raw response: {resp.text[:300]}")
    except Exception as e:
        log.append(f"❌ Gemini error: {e}")
    return []

# ── REPORTS ────────────────────────────────────────────────────────────────

def write_reports(all_raw: dict, all_scored: list[dict]) -> list[str]:
    ranked = sorted(all_scored, key=lambda x: x.get("numeric_score", 0), reverse=True)

    with open("raw_discovery.txt", "w", encoding="utf-8") as f:
        for loc, jobs in all_raw.items():
            f.write(f"\n{'='*50}\n{loc} — {len(jobs)} listings\n{'='*50}\n")
            for j in jobs:
                f.write(f"\n  {j['title']} @ {j['company']}\n")
                f.write(f"  {j['location']} | {j['seniority']} | {j['posted']}\n")
                f.write(f"  {j['url']}\n")

    with open("scored_results.txt", "w", encoding="utf-8") as f:
        f.write("=== DIAGNOSTICS ===\n")
        for m in log: f.write(f"  {m}\n")
        f.write("\n=== ALL SCORES (ranked) ===\n")
        for j in ranked:
            flag = j.get("flag", "")
            f.write(f"\n[{j.get('numeric_score')}/10] [{flag}] {j.get('title')} @ {j.get('company')}\n")
            f.write(f"  {j.get('rationale')}\n")
            f.write(f"  {j.get('url')}\n")

    return ["raw_discovery.txt", "scored_results.txt"]

# ── EMAIL ──────────────────────────────────────────────────────────────────

def build_email(matches: list[dict], all_scored: list[dict]) -> str:
    ranked = sorted(matches, key=lambda x: x.get("numeric_score", 0), reverse=True)

    flag_colors = {"APPLY": "#1a7a4a", "REVIEW": "#b45309", "SKIP": "#8e8e93"}

    cards = ""
    for m in ranked:
        flag = m.get("flag", "REVIEW")
        fc = flag_colors.get(flag, "#8e8e93")
        cards += f"""
        <div style='border:1px solid #e5e5ea;border-radius:10px;padding:16px;margin:10px 0;'>
          <div style='display:flex;justify-content:space-between;align-items:flex-start;'>
            <strong style='font-size:15px;flex:1;'>{m.get('title')}</strong>
            <div style='display:flex;gap:6px;margin-left:12px;flex-shrink:0;'>
              <span style='background:{fc};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;'>{flag}</span>
              <span style='background:#1a7a4a;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;'>{m.get('numeric_score')}/10</span>
            </div>
          </div>
          <div style='color:#48484a;font-size:13px;margin:4px 0 8px;'>{m.get('company')}</div>
          <p style='color:#48484a;font-size:13px;margin:0 0 10px;'>{m.get('rationale','')}</p>
          <a href='{m.get('url','#')}' style='color:#1a7a4a;font-weight:600;font-size:13px;text-decoration:none;
             border:1.5px solid #1a7a4a;padding:5px 14px;border-radius:20px;'>View listing</a>
        </div>"""

    apply_count  = sum(1 for m in matches if m.get("flag") == "APPLY")
    review_count = sum(1 for m in matches if m.get("flag") == "REVIEW")

    summary = f"""
    <div style='background:#f9f8f5;border-radius:10px;padding:16px;margin-bottom:20px;'>
      <strong>This run:</strong> {len(all_scored)} evaluated ·
      <span style='color:#1a7a4a;font-weight:600;'>{apply_count} APPLY</span> ·
      <span style='color:#b45309;font-weight:600;'>{review_count} REVIEW</span> ·
      Threshold: {SCORE_THRESHOLD}/10
    </div>"""

    body = f"<h2 style='color:#1a7a4a;border-bottom:2px solid #1a7a4a;padding-bottom:8px;'>Executive Job Digest</h2>"
    if matches:
        body += summary + cards
    else:
        body += summary + "<p>No listings above threshold. Full results in attached reports.</p>"

    return f"<html><body style='font-family:sans-serif;max-width:620px;margin:auto;color:#1c1c1e;padding:20px;'>{body}</body></html>"


def send_email(html: str, attachments: list[str]) -> None:
    addr, pwd = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]
    matches_count = html.count("View listing")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Job Digest — {matches_count} match(es) this run"
    msg["From"] = msg["To"] = addr
    msg.attach(MIMEText(html, "html"))

    for path in attachments:
        if not os.path.exists(path): continue
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream"); part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls(); s.login(addr, pwd); s.send_message(msg)
    log.append("✅ Email delivered")

# ── MAIN ───────────────────────────────────────────────────────────────────

def main() -> None:
    all_raw: dict = {}
    all_scored: list = []

    for cfg in SEARCH_CONFIG:
        raw   = fetch_jobs(cfg["location"])
        normd = [normalise(j) for j in raw]
        all_raw[cfg["location"]] = normd
        if normd:
            all_scored.extend(score_jobs(normd))

    reports = write_reports(all_raw, all_scored)
    matches = [j for j in all_scored if j.get("numeric_score", 0) >= SCORE_THRESHOLD]

    try:
        html = build_email(matches, all_scored)
        send_email(html, reports)
    except Exception as e:
        log.append(f"❌ Email failed: {e}")

    print("\n".join(log))


if __name__ == "__main__":
    main()
