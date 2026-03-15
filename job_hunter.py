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
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import (
    ADZUNA_APP_ID, ADZUNA_APP_KEY,
    REED_API_KEY,
    EMAIL_ENABLED, EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECIPIENT, SMTP_SERVER, SMTP_PORT,
    LOCATION, MIN_SALARY, MAX_SALARY, DISTANCE_MILES,
    SEARCH_ROLES, OUTPUT_DIR, JOBS_DB_FILE, HTML_REPORT_FILE,
)


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
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for r in data.get("results", []):
            salary_min = r.get("salary_min") or r.get("salary_is_predicted") and MIN_SALARY or 0
            jobs.append({
                "title": r.get("title", ""),
                "company": r.get("company", {}).get("display_name", "Unknown"),
                "location": r.get("location", {}).get("display_name", LOCATION),
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
        ctx = ssl.create_default_context()
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
                "location": r.get("locationName", LOCATION),
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

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_count = len(new_job_ids)
    total_count = len(jobs_list)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>London Job Hunter - Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}
        .header {{ text-align: center; margin-bottom: 2rem; }}
        .header h1 {{ font-size: 2rem; color: #60a5fa; }}
        .header p {{ color: #94a3b8; margin-top: 0.5rem; }}
        .stats {{ display: flex; gap: 1rem; justify-content: center; margin-bottom: 2rem; }}
        .stat {{ background: #1e293b; padding: 1rem 2rem; border-radius: 12px; text-align: center; }}
        .stat-number {{ font-size: 1.5rem; font-weight: bold; color: #60a5fa; }}
        .stat-label {{ font-size: 0.85rem; color: #94a3b8; }}
        .filters {{ display: flex; gap: 0.5rem; justify-content: center; margin-bottom: 2rem; flex-wrap: wrap; }}
        .filter-btn {{ padding: 0.5rem 1rem; border-radius: 8px; border: 1px solid #334155; background: #1e293b; color: #e2e8f0; cursor: pointer; font-size: 0.85rem; }}
        .filter-btn:hover, .filter-btn.active {{ background: #3b82f6; border-color: #3b82f6; }}
        .job-grid {{ display: grid; gap: 1rem; max-width: 900px; margin: 0 auto; }}
        .job-card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; border-left: 4px solid #3b82f6; transition: transform 0.2s; }}
        .job-card:hover {{ transform: translateX(4px); }}
        .job-card.new {{ border-left-color: #22c55e; }}
        .job-title {{ font-size: 1.1rem; font-weight: 600; color: #f1f5f9; }}
        .job-company {{ color: #60a5fa; margin-top: 0.25rem; }}
        .job-meta {{ display: flex; gap: 1rem; margin-top: 0.5rem; flex-wrap: wrap; }}
        .job-meta span {{ font-size: 0.8rem; color: #94a3b8; }}
        .job-desc {{ font-size: 0.85rem; color: #cbd5e1; margin-top: 0.75rem; line-height: 1.5; }}
        .job-tags {{ display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }}
        .tag {{ font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 6px; background: #334155; color: #94a3b8; }}
        .tag.new {{ background: #166534; color: #86efac; }}
        .tag.source {{ background: #1e3a5f; color: #93c5fd; }}
        .apply-btn {{ display: inline-block; margin-top: 0.75rem; padding: 0.5rem 1rem; background: #3b82f6; color: white; text-decoration: none; border-radius: 8px; font-size: 0.85rem; }}
        .apply-btn:hover {{ background: #2563eb; }}
        .no-salary {{ color: #64748b; font-style: italic; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>London Job Hunter</h1>
        <p>Last updated: {now}</p>
    </div>
    <div class="stats">
        <div class="stat">
            <div class="stat-number">{total_count}</div>
            <div class="stat-label">Total Jobs</div>
        </div>
        <div class="stat">
            <div class="stat-number">{new_count}</div>
            <div class="stat-label">New This Run</div>
        </div>
    </div>
    <div class="filters">
        <button class="filter-btn active" onclick="filterJobs('all')">All</button>
        <button class="filter-btn" onclick="filterJobs('new')">New Only</button>
        <button class="filter-btn" onclick="filterJobs('Adzuna')">Adzuna</button>
        <button class="filter-btn" onclick="filterJobs('Reed')">Reed</button>
    </div>
    <div class="job-grid">
"""

    for job in jobs_list:
        jid = job_id(job)
        is_new = jid in new_job_ids
        new_class = "new" if is_new else ""

        salary_str = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"£{int(job['salary_min']):,} - £{int(job['salary_max']):,}"
        elif job.get("salary_min"):
            salary_str = f"From £{int(job['salary_min']):,}"
        elif job.get("salary_max"):
            salary_str = f"Up to £{int(job['salary_max']):,}"

        salary_html = salary_str if salary_str else '<span class="no-salary">Salary not listed</span>'

        html += f"""
        <div class="job-card {new_class}" data-source="{job.get('source', '')}" data-new="{str(is_new).lower()}">
            <div class="job-title">{job.get('title', 'Untitled')}</div>
            <div class="job-company">{job.get('company', 'Unknown')}</div>
            <div class="job-meta">
                <span>{job.get('location', '')}</span>
                <span>{salary_html}</span>
                <span>{job.get('date_posted', '')[:10]}</span>
            </div>
            <div class="job-desc">{job.get('description', '')}</div>
            <div class="job-tags">
                <span class="tag source">{job.get('source', '')}</span>
                {f'<span class="tag new">NEW</span>' if is_new else ''}
                {f'<span class="tag">{job.get("contract_type")}</span>' if job.get("contract_type") else ''}
            </div>
            <a href="{job.get('url', '#')}" target="_blank" class="apply-btn">View & Apply</a>
        </div>
"""

    html += """
    </div>
    <script>
        function filterJobs(filter) {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            document.querySelectorAll('.job-card').forEach(card => {
                if (filter === 'all') { card.style.display = 'block'; }
                else if (filter === 'new') { card.style.display = card.dataset.new === 'true' ? 'block' : 'none'; }
                else { card.style.display = card.dataset.source === filter ? 'block' : 'none'; }
            });
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
