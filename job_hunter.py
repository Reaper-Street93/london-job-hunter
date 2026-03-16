#!/usr/bin/env python3
"""
London Job Hunter
Automatically searches for entry-level tech/business roles in London
and generates reports + optional email notifications.
"""

import json
import os
import sys
import time
import hashlib
import smtplib
import urllib.request
import urllib.parse
import urllib.error
import base64
import ssl
import certifi
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import (
    ADZUNA_APP_ID, ADZUNA_APP_KEY,
    REED_API_KEY,
    EMAIL_ENABLED, EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECIPIENT, SMTP_SERVER, SMTP_PORT,
    LOCATION, MIN_SALARY, MAX_SALARY, DISTANCE_MILES,
    SEARCH_ROLES, EXCLUDE_KEYWORDS, FULL_TIME_ONLY,
    OUTPUT_DIR, JOBS_DB_FILE, HTML_REPORT_FILE,
)


# London postcode area mapping
LONDON_POSTCODES = {
    "EC": "City of London", "WC": "West End",
    "E": "East London", "N": "North London", "NW": "North West London",
    "SE": "South East London", "SW": "South West London",
    "W": "West London",
    "BR": "Bromley", "CR": "Croydon", "DA": "Dartford",
    "EN": "Enfield", "HA": "Harrow", "IG": "Ilford",
    "KT": "Kingston", "RM": "Romford", "SM": "Sutton",
    "TW": "Twickenham", "UB": "Uxbridge", "WD": "Watford",
}

import re

def format_location(raw_location):
    """Convert raw location/postcode into a readable London area name."""
    if not raw_location:
        return "London"

    loc = raw_location.strip()

    # Try to extract a postcode and map it to an area
    postcode_match = re.match(r'^([A-Z]{1,2})\d', loc.upper())
    if postcode_match:
        prefix = postcode_match.group(1)
        area = LONDON_POSTCODES.get(prefix)
        if area:
            # Format the postcode properly (e.g. "EC2A 1NT")
            formatted = re.sub(r'(\S+)\s*(\d[A-Z]{2})$', r'\1 \2', loc.upper())
            return f"{area}, {formatted}"

    # If it already looks like a readable location, return as-is
    if any(word in loc.lower() for word in ["london", "city", "canary", "shoreditch",
            "westminster", "camden", "islington", "hackney", "tower", "southwark",
            "lambeth", "greenwich", "kensington", "chelsea", "hammersmith",
            "wandsworth", "richmond", "croydon", "bromley", "barnet"]):
        return loc

    # If it's just a postcode with no area match
    postcode_full = re.match(r'^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$', loc.upper())
    if postcode_full:
        formatted = re.sub(r'(\S+)\s*(\d[A-Z]{2})$', r'\1 \2', loc.upper())
        return f"London, {formatted}"

    return loc if loc else "London"


def is_excluded(job):
    """Check if a job should be excluded based on keywords, salary, or contract type."""
    text = f"{job.get('title', '')} {job.get('company', '')} {job.get('description', '')}".lower()

    # Keyword exclusions
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return True

    # Filter out part-time / temp unless it looks like a placement at a known company
    if FULL_TIME_ONLY:
        contract = (job.get("contract_type") or "").lower()
        title = job.get("title", "").lower()
        if "part time" in contract or "part-time" in contract or "part time" in title or "part-time" in title:
            return True

    # Filter out suspiciously low salaries (likely part-time or hourly roles)
    salary = job.get("salary_max") or job.get("salary_min") or 0
    if salary and salary < MIN_SALARY:
        return True

    return False


def load_database():
    """Load existing jobs database."""
    if os.path.exists(JOBS_DB_FILE):
        with open(JOBS_DB_FILE, "r") as f:
            return json.load(f)
    return {"jobs": {}, "last_run": None, "history": []}


def save_database(db):
    """Save jobs database."""
    with open(JOBS_DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)


def job_id(job):
    """Generate a unique ID for a job."""
    raw = f"{job.get('title', '')}-{job.get('company', '')}-{job.get('url', '')}"
    return hashlib.md5(raw.encode()).hexdigest()


# ============================================================
# Adzuna API
# ============================================================

def search_adzuna(role):
    """Search Adzuna for jobs matching a role."""
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        return []

    jobs = []
    params = urllib.parse.urlencode({
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": 20,
        "what": role,
        "where": LOCATION,
        "distance": DISTANCE_MILES,
        "salary_min": MIN_SALARY,
        "salary_max": MAX_SALARY,
        "sort_by": "date",
        "max_days_old": 14,
    })

    url = f"https://api.adzuna.com/v1/api/jobs/gb/search/1?{params}"

    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for r in data.get("results", []):
            salary_min = r.get("salary_min") or r.get("salary_is_predicted") and MIN_SALARY or 0
            jobs.append({
                "title": r.get("title", ""),
                "company": r.get("company", {}).get("display_name", "Unknown"),
                "location": format_location(r.get("location", {}).get("display_name", LOCATION)),
                "salary_min": r.get("salary_min"),
                "salary_max": r.get("salary_max"),
                "description": r.get("description", "")[:300],
                "url": r.get("redirect_url", ""),
                "date_posted": r.get("created", ""),
                "source": "Adzuna",
                "contract_type": r.get("contract_type", ""),
                "category": r.get("category", {}).get("label", ""),
            })
    except Exception as e:
        print(f"  [Adzuna] Error searching '{role}': {e}")

    return jobs


# ============================================================
# Reed API
# ============================================================

def search_reed(role):
    """Search Reed for jobs matching a role."""
    if not REED_API_KEY:
        return []

    jobs = []
    params = urllib.parse.urlencode({
        "keywords": role,
        "locationName": LOCATION,
        "distancefromlocation": DISTANCE_MILES,
        "minimumSalary": MIN_SALARY,
        "maximumSalary": MAX_SALARY,
        "resultsToTake": 20,
        "graduate": "true",
    })

    url = f"https://www.reed.co.uk/api/1.0/search?{params}"

    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        # Reed uses basic auth with API key as username, empty password
        credentials = base64.b64encode(f"{REED_API_KEY}:".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {credentials}")

        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for r in data.get("results", []):
            jobs.append({
                "title": r.get("jobTitle", ""),
                "company": r.get("employerName", "Unknown"),
                "location": format_location(r.get("locationName", LOCATION)),
                "salary_min": r.get("minimumSalary"),
                "salary_max": r.get("maximumSalary"),
                "description": r.get("jobDescription", "")[:300],
                "url": f"https://www.reed.co.uk/jobs/{r.get('jobId', '')}",
                "date_posted": r.get("date", ""),
                "source": "Reed",
                "contract_type": r.get("contractType", ""),
                "category": r.get("jobTitle", ""),
            })
    except Exception as e:
        print(f"  [Reed] Error searching '{role}': {e}")

    return jobs


# ============================================================
# Report Generation
# ============================================================

def generate_html_report(db, new_job_ids):
    """Generate an HTML report of all current jobs."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    jobs_list = sorted(
        db["jobs"].values(),
        key=lambda x: x.get("date_posted", ""),
        reverse=True,
    )

    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    new_count = len(new_job_ids)
    total_count = len(jobs_list)

    # Count sources and unique companies
    sources = {}
    companies = set()
    for j in jobs_list:
        src = j.get("source", "Unknown")
        sources[src] = sources.get(src, 0) + 1
        companies.add(j.get("company", ""))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>London Job Hunter</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        :root {{
            --bg-primary: #101018;
            --bg-secondary: #1a1a26;
            --bg-card: #1e1e2e;
            --bg-card-hover: #262638;
            --border: #363650;
            --text-primary: #f4f4f7;
            --text-secondary: #a0a0be;
            --text-muted: #6e6e8a;
            --accent: #7c7ff7;
            --accent-light: #a5a8fc;
            --accent-glow: rgba(124, 127, 247, 0.18);
            --green: #34d399;
            --green-glow: rgba(52, 211, 153, 0.18);
            --amber: #fbbf24;
            --rose: #fb7185;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }}

        /* ---- Animated background ---- */
        .bg-grid {{
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background-image:
                linear-gradient(rgba(99, 102, 241, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(99, 102, 241, 0.03) 1px, transparent 1px);
            background-size: 60px 60px;
            pointer-events: none;
            z-index: 0;
        }}

        .bg-glow {{
            position: fixed;
            width: 600px; height: 600px;
            border-radius: 50%;
            filter: blur(120px);
            opacity: 0.07;
            pointer-events: none;
            z-index: 0;
        }}

        .bg-glow-1 {{ top: -200px; left: -200px; background: var(--accent); }}
        .bg-glow-2 {{ bottom: -200px; right: -200px; background: var(--green); }}

        .container {{
            position: relative;
            z-index: 1;
            max-width: 1100px;
            margin: 0 auto;
            padding: 2rem 1.5rem;
        }}

        /* ---- Header ---- */
        .header {{
            text-align: center;
            padding: 3rem 0 2rem;
        }}

        .header-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.4rem 1rem;
            background: var(--accent-glow);
            border: 1px solid rgba(99, 102, 241, 0.2);
            border-radius: 100px;
            font-size: 0.75rem;
            font-weight: 500;
            color: var(--accent-light);
            letter-spacing: 0.05em;
            text-transform: uppercase;
            margin-bottom: 1rem;
        }}

        .header-badge .pulse {{
            width: 6px; height: 6px;
            background: var(--green);
            border-radius: 50%;
            animation: pulse 2s ease-in-out infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }}
            50% {{ opacity: 0.7; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }}
        }}

        .header h1 {{
            font-size: 2.5rem;
            font-weight: 800;
            letter-spacing: -0.03em;
            background: linear-gradient(135deg, var(--text-primary) 0%, var(--accent-light) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .header .subtitle {{
            color: var(--text-secondary);
            margin-top: 0.5rem;
            font-size: 0.95rem;
            font-weight: 300;
        }}

        /* ---- Stats ---- */
        .stats {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
            margin-bottom: 2rem;
        }}

        .stat {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.25rem;
            text-align: center;
            transition: border-color 0.3s;
        }}

        .stat:hover {{ border-color: var(--accent); }}

        .stat-number {{
            font-size: 2rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1;
        }}

        .stat-number.accent {{ color: var(--accent-light); }}
        .stat-number.green {{ color: var(--green); }}
        .stat-number.amber {{ color: var(--amber); }}

        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-top: 0.5rem;
        }}

        /* ---- Controls ---- */
        .controls {{
            display: flex;
            gap: 0.75rem;
            align-items: center;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
        }}

        .search-box {{
            flex: 1;
            min-width: 200px;
            position: relative;
        }}

        .search-box input {{
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.5rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.3s;
        }}

        .search-box input:focus {{ border-color: var(--accent); }}
        .search-box input::placeholder {{ color: var(--text-muted); }}

        .search-box svg {{
            position: absolute;
            left: 0.85rem;
            top: 50%;
            transform: translateY(-50%);
            width: 16px; height: 16px;
            color: var(--text-muted);
        }}

        .filter-group {{
            display: flex;
            gap: 0.35rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 0.25rem;
        }}

        .filter-btn {{
            padding: 0.5rem 1rem;
            border-radius: 10px;
            border: none;
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            font-size: 0.8rem;
            font-weight: 500;
            transition: all 0.2s;
        }}

        .filter-btn:hover {{ color: var(--text-primary); }}

        .filter-btn.active {{
            background: var(--accent);
            color: white;
            box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
        }}

        .sort-select {{
            padding: 0.65rem 1rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            font-size: 0.8rem;
            outline: none;
            cursor: pointer;
        }}

        /* ---- Job Cards ---- */
        .job-list {{ display: grid; gap: 0.75rem; }}

        .job-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.5rem;
            transition: all 0.25s ease;
            cursor: default;
            position: relative;
            overflow: hidden;
        }}

        .job-card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0;
            width: 3px; height: 100%;
            background: var(--accent);
            opacity: 0;
            transition: opacity 0.3s;
        }}

        .job-card:hover {{
            background: var(--bg-card-hover);
            border-color: rgba(99, 102, 241, 0.3);
            transform: translateY(-1px);
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2);
        }}

        .job-card:hover::before {{ opacity: 1; }}

        .job-card.new::before {{
            background: var(--green);
            opacity: 1;
        }}

        .job-card-top {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
        }}

        .job-title {{
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.3;
        }}

        .job-company {{
            font-size: 0.9rem;
            color: var(--accent-light);
            font-weight: 500;
            margin-top: 0.2rem;
        }}

        .job-salary {{
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--green);
            white-space: nowrap;
        }}

        .job-salary.unknown {{
            color: var(--text-muted);
            font-weight: 400;
            font-size: 0.8rem;
        }}

        .job-meta {{
            display: flex;
            gap: 1.25rem;
            margin-top: 0.6rem;
            flex-wrap: wrap;
        }}

        .job-meta-item {{
            display: flex;
            align-items: center;
            gap: 0.35rem;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}

        .job-meta-item svg {{ width: 14px; height: 14px; opacity: 0.5; }}

        .job-desc {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-top: 0.75rem;
            line-height: 1.6;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .job-footer {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 1rem;
        }}

        .job-tags {{
            display: flex;
            gap: 0.4rem;
            flex-wrap: wrap;
        }}

        .tag {{
            font-size: 0.7rem;
            padding: 0.25rem 0.65rem;
            border-radius: 6px;
            font-weight: 500;
            letter-spacing: 0.02em;
        }}

        .tag-source {{
            background: var(--accent-glow);
            color: var(--accent-light);
            border: 1px solid rgba(99, 102, 241, 0.15);
        }}

        .tag-new {{
            background: var(--green-glow);
            color: var(--green);
            border: 1px solid rgba(16, 185, 129, 0.15);
            animation: fadeIn 0.5s ease;
        }}

        .tag-type {{
            background: rgba(245, 158, 11, 0.1);
            color: var(--amber);
            border: 1px solid rgba(245, 158, 11, 0.15);
        }}

        @keyframes fadeIn {{ from {{ opacity: 0; transform: scale(0.9); }} to {{ opacity: 1; transform: scale(1); }} }}

        .apply-btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.5rem 1.25rem;
            background: var(--accent);
            color: white;
            text-decoration: none;
            border-radius: 10px;
            font-size: 0.8rem;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            transition: all 0.2s;
            white-space: nowrap;
        }}

        .apply-btn:hover {{
            background: var(--accent-light);
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
            transform: translateY(-1px);
        }}

        .apply-btn svg {{ width: 14px; height: 14px; }}

        /* ---- Empty state ---- */
        .empty-state {{
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-muted);
        }}

        /* ---- Results count ---- */
        .results-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            padding: 0 0.25rem;
        }}

        .results-count {{
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}

        .results-count strong {{ color: var(--text-primary); }}

        /* ---- Responsive ---- */
        @media (max-width: 768px) {{
            .stats {{ grid-template-columns: repeat(2, 1fr); }}
            .controls {{ flex-direction: column; }}
            .search-box {{ min-width: 100%; }}
            .header h1 {{ font-size: 1.75rem; }}
            .job-card-top {{ flex-direction: column; gap: 0.25rem; }}
        }}

        /* ---- Scrollbar ---- */
        ::-webkit-scrollbar {{ width: 6px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg-primary); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}

        /* ---- Footer ---- */
        .page-footer {{
            text-align: center;
            padding: 3rem 0 2rem;
            color: var(--text-muted);
            font-size: 0.8rem;
        }}
        .page-footer a {{ color: var(--accent-light); text-decoration: none; }}
    </style>
</head>
<body>
    <div class="bg-grid"></div>
    <div class="bg-glow bg-glow-1"></div>
    <div class="bg-glow bg-glow-2"></div>

    <div class="container">
        <div class="header">
            <div class="header-badge">
                <span class="pulse"></span>
                Auto-scanning London jobs
            </div>
            <h1>London Job Hunter</h1>
            <p class="subtitle">Last scan: {now}</p>
        </div>

        <div class="stats">
            <div class="stat">
                <div class="stat-number accent">{total_count}</div>
                <div class="stat-label">Total Jobs</div>
            </div>
            <div class="stat">
                <div class="stat-number green">{new_count}</div>
                <div class="stat-label">New This Run</div>
            </div>
            <div class="stat">
                <div class="stat-number amber">{len(companies)}</div>
                <div class="stat-label">Companies</div>
            </div>
            <div class="stat">
                <div class="stat-number">{len(sources)}</div>
                <div class="stat-label">Sources</div>
            </div>
        </div>

        <div class="controls">
            <div class="search-box">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                <input type="text" id="searchInput" placeholder="Search jobs, companies, keywords..." oninput="applyFilters()">
            </div>
            <div class="filter-group">
                <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
                <button class="filter-btn" onclick="setFilter('new', this)">New</button>
                <button class="filter-btn" onclick="setFilter('Adzuna', this)">Adzuna</button>
                <button class="filter-btn" onclick="setFilter('Reed', this)">Reed</button>
            </div>
            <select class="sort-select" id="sortSelect" onchange="applyFilters()">
                <option value="date">Newest first</option>
                <option value="salary-high">Salary: High to Low</option>
                <option value="salary-low">Salary: Low to High</option>
                <option value="company">Company A-Z</option>
            </select>
        </div>

        <div class="results-bar">
            <span class="results-count" id="resultsCount">Showing <strong>{total_count}</strong> jobs</span>
        </div>

        <div class="job-list" id="jobList">
"""

    for job in jobs_list:
        jid = job_id(job)
        is_new = jid in new_job_ids
        new_class = "new" if is_new else ""

        salary_str = ""
        salary_val = 0
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"&#163;{int(job['salary_min']):,} - &#163;{int(job['salary_max']):,}"
            salary_val = int(job["salary_min"])
        elif job.get("salary_min"):
            salary_str = f"From &#163;{int(job['salary_min']):,}"
            salary_val = int(job["salary_min"])
        elif job.get("salary_max"):
            salary_str = f"Up to &#163;{int(job['salary_max']):,}"
            salary_val = int(job["salary_max"])

        salary_html = f'<span class="job-salary">{salary_str}</span>' if salary_str else '<span class="job-salary unknown">Salary unlisted</span>'

        # Sanitize description for HTML
        desc = job.get('description', '').replace('<', '&lt;').replace('>', '&gt;')
        title = job.get('title', 'Untitled').replace('<', '&lt;').replace('>', '&gt;')
        company = job.get('company', 'Unknown').replace('<', '&lt;').replace('>', '&gt;')

        html += f"""
            <div class="job-card {new_class}" data-source="{job.get('source', '')}" data-new="{str(is_new).lower()}" data-salary="{salary_val}" data-company="{company.lower()}" data-date="{job.get('date_posted', '')}">
                <div class="job-card-top">
                    <div>
                        <div class="job-title">{title}</div>
                        <div class="job-company">{company}</div>
                    </div>
                    {salary_html}
                </div>
                <div class="job-meta">
                    <span class="job-meta-item">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/><path d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z"/></svg>
                        {job.get('location', '')}
                    </span>
                    <span class="job-meta-item">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"/></svg>
                        {job.get('date_posted', '')[:10]}
                    </span>
                </div>
                <div class="job-desc">{desc}</div>
                <div class="job-footer">
                    <div class="job-tags">
                        <span class="tag tag-source">{job.get('source', '')}</span>
                        {f'<span class="tag tag-new">NEW</span>' if is_new else ''}
                        {f'<span class="tag tag-type">{job.get("contract_type")}</span>' if job.get("contract_type") else ''}
                    </div>
                    <a href="{job.get('url', '#')}" target="_blank" class="apply-btn">
                        Apply
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
                    </a>
                </div>
            </div>
"""

    html += """
        </div>

        <div class="page-footer">
            London Job Hunter &middot; Auto-generated report<br>
            <a href="https://github.com/Reaper-Street93/london-job-hunter">View on GitHub</a>
        </div>
    </div>

    <script>
        let currentFilter = 'all';

        function setFilter(filter, btn) {
            currentFilter = filter;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyFilters();
        }

        function applyFilters() {
            const search = document.getElementById('searchInput').value.toLowerCase();
            const sort = document.getElementById('sortSelect').value;
            const cards = Array.from(document.querySelectorAll('.job-card'));
            let visible = 0;

            // Sort
            cards.sort((a, b) => {
                if (sort === 'date') return (b.dataset.date || '').localeCompare(a.dataset.date || '');
                if (sort === 'salary-high') return (parseInt(b.dataset.salary) || 0) - (parseInt(a.dataset.salary) || 0);
                if (sort === 'salary-low') return (parseInt(a.dataset.salary) || 0) - (parseInt(b.dataset.salary) || 0);
                if (sort === 'company') return (a.dataset.company || '').localeCompare(b.dataset.company || '');
                return 0;
            });

            const list = document.getElementById('jobList');
            cards.forEach(card => list.appendChild(card));

            // Filter
            cards.forEach(card => {
                const text = card.textContent.toLowerCase();
                const matchesSearch = !search || text.includes(search);
                const matchesFilter =
                    currentFilter === 'all' ||
                    (currentFilter === 'new' && card.dataset.new === 'true') ||
                    card.dataset.source === currentFilter;

                const show = matchesSearch && matchesFilter;
                card.style.display = show ? 'block' : 'none';
                if (show) visible++;
            });

            document.getElementById('resultsCount').innerHTML = `Showing <strong>${visible}</strong> jobs`;
        }
    </script>
</body>
</html>"""

    with open(HTML_REPORT_FILE, "w") as f:
        f.write(html)

    print(f"  HTML report saved to {HTML_REPORT_FILE}")


# ============================================================
# Email Notification
# ============================================================

def send_email_notification(new_jobs):
    """Send email with new job listings."""
    if not EMAIL_ENABLED or not new_jobs:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"London Job Hunter: {len(new_jobs)} new jobs found!"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    text_body = f"Found {len(new_jobs)} new jobs:\n\n"
    html_body = f"""<h2 style="color:#3b82f6;">London Job Hunter - {len(new_jobs)} New Jobs</h2><hr>"""

    for job in new_jobs:
        salary_str = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"£{int(job['salary_min']):,} - £{int(job['salary_max']):,}"

        text_body += f"- {job['title']} at {job['company']} ({salary_str})\n  {job['url']}\n\n"
        html_body += f"""
        <div style="margin:1rem 0;padding:1rem;border-left:3px solid #3b82f6;background:#f8fafc;">
            <strong>{job['title']}</strong><br>
            <span style="color:#3b82f6;">{job['company']}</span> | {job.get('location','')} | {salary_str}<br>
            <p style="color:#64748b;font-size:0.9rem;">{job.get('description','')[:200]}</p>
            <a href="{job['url']}" style="color:#3b82f6;">View & Apply</a>
        </div>
        """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"  Email sent to {EMAIL_RECIPIENT}")
    except Exception as e:
        print(f"  Email failed: {e}")


# ============================================================
# Main
# ============================================================

def run():
    """Main job hunting routine."""
    print("=" * 55)
    print("  LONDON JOB HUNTER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    db = load_database()
    all_new_jobs = []
    new_job_ids = set()

    # Mark old jobs for expiry tracking
    for jid in db["jobs"]:
        db["jobs"][jid]["seen_this_run"] = False

    total_roles = len(SEARCH_ROLES)
    for i, role in enumerate(SEARCH_ROLES, 1):
        print(f"\n[{i}/{total_roles}] Searching: {role}")

        # Search both sources
        adzuna_jobs = search_adzuna(role)
        reed_jobs = search_reed(role)
        combined = adzuna_jobs + reed_jobs

        print(f"  Found {len(adzuna_jobs)} (Adzuna) + {len(reed_jobs)} (Reed) = {len(combined)} results")

        for job in combined:
            if is_excluded(job):
                continue
            jid = job_id(job)
            job["seen_this_run"] = True

            if jid not in db["jobs"]:
                job["first_seen"] = datetime.now().isoformat()
                job["status"] = "new"
                db["jobs"][jid] = job
                all_new_jobs.append(job)
                new_job_ids.add(jid)
            else:
                db["jobs"][jid]["seen_this_run"] = True
                db["jobs"][jid]["last_seen"] = datetime.now().isoformat()

        # Be nice to the APIs
        time.sleep(1)

    # Remove jobs not seen for 3+ runs (likely expired)
    expired = []
    for jid, job in db["jobs"].items():
        if not job.get("seen_this_run", False):
            runs_unseen = job.get("runs_unseen", 0) + 1
            job["runs_unseen"] = runs_unseen
            if runs_unseen >= 3:
                expired.append(jid)
        else:
            job["runs_unseen"] = 0

    for jid in expired:
        del db["jobs"][jid]

    # Clean up temp field
    for jid in db["jobs"]:
        db["jobs"][jid].pop("seen_this_run", None)

    # Update metadata
    db["last_run"] = datetime.now().isoformat()
    db["history"].append({
        "date": datetime.now().isoformat(),
        "new_jobs": len(all_new_jobs),
        "total_jobs": len(db["jobs"]),
        "expired": len(expired),
    })

    # Keep only last 50 history entries
    db["history"] = db["history"][-50:]

    # Save database
    save_database(db)

    # Generate report
    print(f"\n{'=' * 55}")
    print(f"  RESULTS SUMMARY")
    print(f"  New jobs found:  {len(all_new_jobs)}")
    print(f"  Total active:    {len(db['jobs'])}")
    print(f"  Expired/removed: {len(expired)}")
    print(f"{'=' * 55}")

    generate_html_report(db, new_job_ids)

    # Send email if enabled
    if EMAIL_ENABLED and all_new_jobs:
        send_email_notification(all_new_jobs)
    elif not EMAIL_ENABLED:
        print("  Email notifications disabled (set EMAIL_ENABLED=True in config.py)")

    print(f"\n  Done! Open {HTML_REPORT_FILE} in your browser to view jobs.")
    return len(all_new_jobs)


if __name__ == "__main__":
    run()
