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
import re
import csv
import certifi
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from difflib import SequenceMatcher

from config import (
    ADZUNA_APP_ID, ADZUNA_APP_KEY,
    REED_API_KEY,
    EMAIL_ENABLED, EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECIPIENT, SMTP_SERVER, SMTP_PORT,
    LOCATION, MIN_SALARY, MAX_SALARY, DISTANCE_MILES,
    SEARCH_ROLES, EXCLUDE_KEYWORDS, FULL_TIME_ONLY,
    OUTPUT_DIR, JOBS_DB_FILE, HTML_REPORT_FILE,
)

# Try to import optional API keys
try:
    from config import JOOBLE_API_KEY
except ImportError:
    JOOBLE_API_KEY = ""

try:
    from config import FINDWORK_API_KEY
except ImportError:
    FINDWORK_API_KEY = ""

try:
    from config import FINDWORK_API_KEY
except ImportError:
    FINDWORK_API_KEY = ""


# ============================================================
# Location Formatting
# ============================================================

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


def format_location(raw_location):
    """Convert raw location/postcode into a readable London area name."""
    if not raw_location:
        return "London"
    loc = raw_location.strip()
    postcode_match = re.match(r'^([A-Z]{1,2})\d', loc.upper())
    if postcode_match:
        prefix = postcode_match.group(1)
        area = LONDON_POSTCODES.get(prefix)
        if area:
            formatted = re.sub(r'(\S+)\s*(\d[A-Z]{2})$', r'\1 \2', loc.upper())
            return f"{area}, {formatted}"
    if any(word in loc.lower() for word in ["london", "city", "canary", "shoreditch",
            "westminster", "camden", "islington", "hackney", "tower", "southwark",
            "lambeth", "greenwich", "kensington", "chelsea", "hammersmith",
            "wandsworth", "richmond", "croydon", "bromley", "barnet"]):
        return loc
    postcode_full = re.match(r'^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$', loc.upper())
    if postcode_full:
        formatted = re.sub(r'(\S+)\s*(\d[A-Z]{2})$', r'\1 \2', loc.upper())
        return f"London, {formatted}"
    return loc if loc else "London"


# ============================================================
# URL Resolution & Validation
# ============================================================

def resolve_url(url, timeout=10):
    """Follow redirects to get the final destination URL (the actual job page)."""
    if not url:
        return url
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        resp = opener.open(req, timeout=timeout)
        final_url = resp.url
        resp.close()
        return final_url
    except urllib.error.HTTPError as e:
        # Some servers don't support HEAD, try GET with no body read
        if e.code in (405, 403):
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
                resp = opener.open(req, timeout=timeout)
                final_url = resp.url
                resp.close()
                return final_url
            except Exception:
                return url
        return url
    except Exception:
        return url


def check_url_alive(url, timeout=10):
    """Check if a URL is still accessible. Returns True unless we get a definitive 404/410."""
    if not url:
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    ctx = ssl.create_default_context(cafile=certifi.where())
    # Try HEAD first
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method)
            for k, v in headers.items():
                req.add_header(k, v)
            resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
            resp.close()
            return True  # Any successful response means it's alive
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return False  # Definitively gone
            if e.code in (403, 401, 405, 406, 429, 503):
                return True  # Blocked but page exists (many sites block bots)
        except Exception:
            if method == "HEAD":
                continue  # Try GET before giving up
            return True  # Network errors = assume alive (don't remove on timeout)
    return True  # Default to keeping the job


def validate_and_clean_urls(db):
    """Check stored job URLs. Only remove jobs with definitively dead links (404/410)."""
    print("\n  Validating job URLs...")
    dead_ids = []
    checked = 0
    total = len(db["jobs"])
    for jid, job in list(db["jobs"].items()):
        url = job.get("url", "")
        if not url or url == "#":
            dead_ids.append(jid)
            continue
        # Only validate URLs older than 3 days (new jobs are fresh)
        first_seen = job.get("first_seen", "")
        if first_seen:
            try:
                seen_dt = datetime.fromisoformat(first_seen)
                if (datetime.now() - seen_dt).days < 3:
                    continue
            except (ValueError, TypeError):
                pass
        checked += 1
        if checked % 20 == 0:
            print(f"    Checked {checked}/{total} URLs...")
        if not check_url_alive(url):
            # Try resolving in case it's a stale redirect
            resolved = resolve_url(url)
            if resolved != url and check_url_alive(resolved):
                db["jobs"][jid]["url"] = resolved
            else:
                dead_ids.append(jid)
        time.sleep(0.3)  # Be polite to servers
    if dead_ids:
        for jid in dead_ids:
            del db["jobs"][jid]
        print(f"    Removed {len(dead_ids)} jobs with dead links (404/410)")
    else:
        print(f"    All {checked} checked URLs are live")
    return dead_ids


def get_reed_detail_url(job_id_num):
    """Fetch Reed job details to get the employer's actual application URL."""
    if not REED_API_KEY:
        return None
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        url = f"https://www.reed.co.uk/api/1.0/jobs/{job_id_num}"
        credentials = base64.b64encode(f"{REED_API_KEY}:".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {credentials}")
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        # externalUrl is the employer's actual application page
        return data.get("externalUrl") or data.get("jobUrl") or None
    except Exception:
        return None


# ============================================================
# Filtering & Deduplication
# ============================================================

def is_excluded(job):
    """Check if a job should be excluded."""
    text = f"{job.get('title', '')} {job.get('company', '')} {job.get('description', '')}".lower()
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return True
    if FULL_TIME_ONLY:
        ct = job.get("contract_type") or ""
        contract = (ct if isinstance(ct, str) else " ".join(ct) if isinstance(ct, list) else str(ct)).lower()
        title = job.get("title", "").lower()
        if "part time" in contract or "part-time" in contract or "part time" in title or "part-time" in title:
            return True
    salary = job.get("salary_max") or job.get("salary_min") or 0
    if salary and salary < MIN_SALARY:
        return True
    return False


def is_duplicate(job, existing_jobs):
    """Check if job is a duplicate of an existing one (same role at same company)."""
    title = job.get("title", "").lower().strip()
    company = job.get("company", "").lower().strip()
    for ej in existing_jobs.values():
        et = ej.get("title", "").lower().strip()
        ec = ej.get("company", "").lower().strip()
        if company == ec and SequenceMatcher(None, title, et).ratio() > 0.85:
            return True
    return False


# ============================================================
# Database
# ============================================================

def load_database():
    if os.path.exists(JOBS_DB_FILE):
        with open(JOBS_DB_FILE, "r") as f:
            return json.load(f)
    return {"jobs": {}, "last_run": None, "history": []}


def save_database(db):
    with open(JOBS_DB_FILE, "w") as f:
        json.dump(db, f, indent=2, default=str)


def job_id(job):
    raw = f"{job.get('title', '')}-{job.get('company', '')}-{job.get('url', '')}"
    return hashlib.md5(raw.encode()).hexdigest()


# ============================================================
# CV Keyword Extraction
# ============================================================

CV_FILE = "cv.txt"

def load_cv_keywords():
    """Load keywords from CV file if it exists."""
    if not os.path.exists(CV_FILE):
        return []
    with open(CV_FILE, "r") as f:
        text = f.read().lower()
    # Extract meaningful words (3+ chars, not common stop words)
    stop_words = {"the", "and", "for", "are", "but", "not", "you", "all", "can",
                  "had", "her", "was", "one", "our", "out", "has", "have", "been",
                  "will", "with", "this", "that", "from", "they", "were", "which",
                  "their", "would", "there", "about", "into", "more", "other",
                  "than", "them", "these", "some", "also", "what", "when", "where"}
    words = re.findall(r'\b[a-z]{3,}\b', text)
    word_counts = {}
    for w in words:
        if w not in stop_words:
            word_counts[w] = word_counts.get(w, 0) + 1
    # Return top keywords sorted by frequency
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
    return [w for w, c in sorted_words[:80]]


def cv_match_score(job, cv_keywords):
    """Score how well a job matches CV keywords (0-100)."""
    if not cv_keywords:
        return 0
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    matches = sum(1 for kw in cv_keywords if kw in text)
    return min(100, int((matches / len(cv_keywords)) * 300))


# ============================================================
# Salary Insights
# ============================================================

def compute_salary_insights(jobs_list):
    """Compute salary statistics from job listings."""
    salaries = []
    for j in jobs_list:
        s_min = j.get("salary_min") or 0
        s_max = j.get("salary_max") or 0
        avg = (s_min + s_max) / 2 if s_min and s_max else s_min or s_max
        if avg >= MIN_SALARY:
            salaries.append(avg)
    if not salaries:
        return {"avg": 0, "median": 0, "min": 0, "max": 0, "count": 0}
    salaries.sort()
    n = len(salaries)
    median = salaries[n // 2] if n % 2 else (salaries[n // 2 - 1] + salaries[n // 2]) / 2
    return {
        "avg": int(sum(salaries) / n),
        "median": int(median),
        "min": int(salaries[0]),
        "max": int(salaries[-1]),
        "count": n,
    }


# ============================================================
# Adzuna API
# ============================================================

def search_adzuna(role):
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
            # Resolve redirect URL to get the actual job page (career site, Workday, etc.)
            raw_redirect = r.get("redirect_url", "")
            cleaned_redirect = re.sub(r'[?&]utm_[^&]*', '', re.sub(r'[?&]se=[^&]*', '', re.sub(r'[?&]v=[^&]*', '', raw_redirect)))
            resolved = resolve_url(cleaned_redirect) if cleaned_redirect else ""
            jobs.append({
                "title": r.get("title", ""),
                "company": r.get("company", {}).get("display_name", "Unknown"),
                "location": format_location(r.get("location", {}).get("display_name", LOCATION)),
                "salary_min": r.get("salary_min"),
                "salary_max": r.get("salary_max"),
                "description": r.get("description", "")[:300],
                "url": resolved or cleaned_redirect,
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
        credentials = base64.b64encode(f"{REED_API_KEY}:".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {credentials}")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for r in data.get("results", []):
            reed_id = r.get("jobId", "")
            reed_page = f"https://www.reed.co.uk/jobs/{reed_id}"
            # Try to get the employer's direct application URL
            direct_url = get_reed_detail_url(reed_id) if reed_id else None
            jobs.append({
                "title": r.get("jobTitle", ""),
                "company": r.get("employerName", "Unknown"),
                "location": format_location(r.get("locationName", LOCATION)),
                "salary_min": r.get("minimumSalary"),
                "salary_max": r.get("maximumSalary"),
                "description": r.get("jobDescription", "")[:300],
                "url": direct_url or reed_page,
                "reed_url": reed_page,
                "date_posted": r.get("date", ""),
                "source": "Reed",
                "contract_type": r.get("contractType", ""),
                "category": r.get("jobTitle", ""),
            })
            time.sleep(0.15)  # Rate limit for detail API calls
    except Exception as e:
        print(f"  [Reed] Error searching '{role}': {e}")
    return jobs


# ============================================================
# Jooble API
# ============================================================

def search_jooble(role):
    """Search Jooble for jobs (free API, aggregates many job boards)."""
    if not JOOBLE_API_KEY:
        return []
    jobs = []
    url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    payload = json.dumps({
        "keywords": role,
        "location": LOCATION,
        "salary": MIN_SALARY,
        "page": 1,
    }).encode()
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for r in data.get("jobs", []):
            salary_text = r.get("salary", "")
            s_min, s_max = parse_salary(salary_text)
            jobs.append({
                "title": r.get("title", ""),
                "company": r.get("company", "Unknown"),
                "location": format_location(r.get("location", LOCATION)),
                "salary_min": s_min,
                "salary_max": s_max,
                "description": (r.get("snippet", "") or "")[:300],
                "url": r.get("link", ""),
                "date_posted": r.get("updated", ""),
                "source": "Jooble",
                "contract_type": r.get("type", ""),
                "category": "",
            })
    except Exception as e:
        print(f"  [Jooble] Error searching '{role}': {e}")
    return jobs


def parse_salary(text):
    """Try to extract min/max salary from text like '£30,000 - £40,000'."""
    if not text:
        return None, None
    numbers = re.findall(r'[\d,]+', text.replace(',', ''))
    nums = [int(n) for n in numbers if int(n) >= 10000]
    if len(nums) >= 2:
        return min(nums), max(nums)
    elif len(nums) == 1:
        return nums[0], nums[0]
    return None, None


# ============================================================
# Findwork API (free tech jobs)
# ============================================================

def search_findwork(role):
    """Search Findwork.dev for tech jobs (free API, good for tech roles)."""
    if not FINDWORK_API_KEY:
        return []
    jobs = []
    params = urllib.parse.urlencode({
        "search": role,
        "location": "london",
        "sort_by": "date",
    })
    url = f"https://findwork.dev/api/jobs/?{params}"
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Token {FINDWORK_API_KEY}")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for r in data.get("results", []):
            s_min, s_max = None, None
            salary_text = r.get("salary", "") or ""
            if salary_text:
                s_min, s_max = parse_salary(salary_text)
            jobs.append({
                "title": r.get("role", ""),
                "company": r.get("company_name", "Unknown"),
                "location": format_location(r.get("location", LOCATION)),
                "salary_min": s_min,
                "salary_max": s_max,
                "description": (r.get("text", "") or "")[:300],
                "url": r.get("url", ""),
                "date_posted": r.get("date_posted", ""),
                "source": "Findwork",
                "contract_type": "Full Time" if not r.get("remote") else "Remote",
                "category": r.get("keywords", [""])[0] if r.get("keywords") else "",
            })
    except Exception as e:
        print(f"  [Findwork] Error searching '{role}': {e}")
    return jobs


# ============================================================
# The Muse API (free, no auth needed, direct URLs)
# ============================================================

def search_themuse(role):
    """Search The Muse for jobs (free API, no key needed, stable direct URLs)."""
    jobs = []
    params = urllib.parse.urlencode({
        "category": "Data Science" if "data" in role.lower() else
                    "Project Management" if "project" in role.lower() else
                    "Business Operations" if "business" in role.lower() else
                    "Customer Service" if "customer" in role.lower() or "success" in role.lower() else
                    "IT",
        "location": "London, United Kingdom",
        "level": "Entry Level",
        "page": 0,
    })
    url = f"https://www.themuse.com/api/public/jobs?{params}"
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for r in data.get("results", []):
            title = r.get("name", "")
            # Filter by role keywords since Muse uses categories not keyword search
            role_words = role.lower().split()
            title_lower = title.lower()
            if not any(w in title_lower for w in role_words if len(w) > 3):
                continue
            company = r.get("company", {}).get("name", "Unknown")
            locations = r.get("locations", [])
            loc_str = locations[0].get("name", "London") if locations else "London"
            landing_url = r.get("refs", {}).get("landing_page", "")
            # Extract salary from contents if present
            contents = r.get("contents", "") or ""
            s_min, s_max = parse_salary(contents[:500])
            jobs.append({
                "title": title,
                "company": company,
                "location": format_location(loc_str),
                "salary_min": s_min,
                "salary_max": s_max,
                "description": re.sub(r'<[^>]+>', '', contents)[:300],
                "url": landing_url,
                "date_posted": r.get("publication_date", ""),
                "source": "The Muse",
                "contract_type": r.get("type", ""),
                "category": (r.get("categories", [{}])[0].get("name", "") if r.get("categories") else ""),
            })
    except Exception as e:
        print(f"  [The Muse] Error searching '{role}': {e}")
    return jobs


# ============================================================
# Jobicy API (free, no auth, remote UK roles)
# ============================================================

def search_jobicy(role):
    """Search Jobicy for remote UK jobs (free API, no key needed)."""
    jobs = []
    params = urllib.parse.urlencode({
        "count": 20,
        "geo": "uk",
        "tag": role.split()[0].lower() if role.split() else "tech",
    })
    url = f"https://jobicy.com/api/v2/remote-jobs?{params}"
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        for r in data.get("jobs", []):
            title = r.get("jobTitle", "")
            # Filter by role keywords
            role_words = role.lower().split()
            title_lower = title.lower()
            if not any(w in title_lower for w in role_words if len(w) > 3):
                continue
            s_min, s_max = None, None
            sal_text = r.get("annualSalaryMin", "")
            if sal_text:
                try:
                    s_min = int(str(sal_text).replace(",", ""))
                except (ValueError, TypeError):
                    pass
            sal_text = r.get("annualSalaryMax", "")
            if sal_text:
                try:
                    s_max = int(str(sal_text).replace(",", ""))
                except (ValueError, TypeError):
                    pass
            jobs.append({
                "title": title,
                "company": r.get("companyName", "Unknown"),
                "location": format_location(r.get("jobGeo", "UK Remote")),
                "salary_min": s_min,
                "salary_max": s_max,
                "description": (r.get("jobExcerpt", "") or "")[:300],
                "url": r.get("url", ""),
                "date_posted": r.get("pubDate", ""),
                "source": "Jobicy",
                "contract_type": r.get("jobType", "Remote"),
                "category": (r.get("jobIndustry", [""])[0] if r.get("jobIndustry") else ""),
            })
    except Exception as e:
        print(f"  [Jobicy] Error searching '{role}': {e}")
    return jobs


# ============================================================
# CSV Export
# ============================================================

def export_csv(db):
    """Export jobs to CSV file."""
    csv_path = os.path.join(OUTPUT_DIR, "jobs_export.csv")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    jobs = sorted(db["jobs"].values(), key=lambda x: x.get("date_posted", ""), reverse=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Company", "Location", "Salary Min", "Salary Max",
                         "Date Posted", "Source", "Contract Type", "URL"])
        for j in jobs:
            writer.writerow([
                j.get("title", ""), j.get("company", ""), j.get("location", ""),
                j.get("salary_min", ""), j.get("salary_max", ""),
                j.get("date_posted", "")[:10], j.get("source", ""),
                j.get("contract_type", ""), j.get("url", ""),
            ])
    print(f"  CSV exported to {csv_path}")


# ============================================================
# Report Generation
# ============================================================

def generate_html_report(db, new_job_ids):
    """Generate the full HTML dashboard with all features."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    jobs_list = sorted(db["jobs"].values(), key=lambda x: x.get("date_posted", ""), reverse=True)
    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    new_count = len(new_job_ids)
    total_count = len(jobs_list)

    # Stats
    sources = {}
    companies = set()
    for j in jobs_list:
        sources[j.get("source", "?")] = sources.get(j.get("source", "?"), 0) + 1
        companies.add(j.get("company", ""))

    salary_stats = compute_salary_insights(jobs_list)

    # CV keywords
    cv_keywords = load_cv_keywords()
    has_cv = len(cv_keywords) > 0

    # Compute CV match for each job
    for j in jobs_list:
        j["cv_score"] = cv_match_score(j, cv_keywords) if has_cv else 0

    # Trend data from history
    history = db.get("history", [])[-20:]
    trend_data = json.dumps([{"date": h["date"][:10], "total": h["total_jobs"], "new": h["new_jobs"]} for h in history])

    # Build job cards data for JavaScript
    jobs_json = []
    for job in jobs_list:
        jid = job_id(job)
        salary_val = 0
        salary_str = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"\u00a3{int(job['salary_min']):,} - \u00a3{int(job['salary_max']):,}"
            salary_val = int(job["salary_min"])
        elif job.get("salary_min"):
            salary_str = f"From \u00a3{int(job['salary_min']):,}"
            salary_val = int(job["salary_min"])
        elif job.get("salary_max"):
            salary_str = f"Up to \u00a3{int(job['salary_max']):,}"
            salary_val = int(job["salary_max"])

        company_slug = re.sub(r'[^a-z0-9]+', '-', job.get('company', '').lower()).strip('-')

        jobs_json.append({
            "id": jid,
            "title": job.get("title", "Untitled"),
            "company": job.get("company", "Unknown"),
            "company_slug": company_slug,
            "location": job.get("location", "London"),
            "salary_str": salary_str,
            "salary_val": salary_val,
            "description": job.get("description", "")[:250],
            "url": job.get("url", "#"),
            "date_posted": job.get("date_posted", "")[:10],
            "source": job.get("source", ""),
            "contract_type": job.get("contract_type", ""),
            "is_new": jid in new_job_ids,
            "cv_score": job.get("cv_score", 0),
            "first_seen": job.get("first_seen", ""),
        })

    jobs_json_str = json.dumps(jobs_json, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>London Job Hunter</title>
    <link rel="manifest" href="manifest.json">
    <meta name="theme-color" content="#101018">
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
            --amber-glow: rgba(251, 191, 36, 0.12);
            --rose: #fb7185;
            --rose-glow: rgba(251, 113, 133, 0.12);
            --cyan: #22d3ee;
        }}

        [data-theme="light"] {{
            --bg-primary: #f8f9fc;
            --bg-secondary: #ffffff;
            --bg-card: #ffffff;
            --bg-card-hover: #f1f3f8;
            --border: #e2e4eb;
            --text-primary: #1a1a2e;
            --text-secondary: #5a5a7a;
            --text-muted: #9090a8;
            --accent: #5b5ef0;
            --accent-light: #6366f1;
            --accent-glow: rgba(91, 94, 240, 0.1);
            --green: #059669;
            --green-glow: rgba(5, 150, 105, 0.1);
            --amber: #d97706;
            --amber-glow: rgba(217, 119, 6, 0.08);
            --rose: #e11d48;
            --rose-glow: rgba(225, 29, 72, 0.08);
            --cyan: #0891b2;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            transition: background 0.3s, color 0.3s;
        }}

        .bg-grid {{
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background-image:
                linear-gradient(rgba(99, 102, 241, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(99, 102, 241, 0.03) 1px, transparent 1px);
            background-size: 60px 60px;
            pointer-events: none; z-index: 0;
        }}

        [data-theme="light"] .bg-grid {{ opacity: 0.5; }}

        .bg-glow {{
            position: fixed; width: 600px; height: 600px; border-radius: 50%;
            filter: blur(120px); opacity: 0.07; pointer-events: none; z-index: 0;
        }}
        .bg-glow-1 {{ top: -200px; left: -200px; background: var(--accent); }}
        .bg-glow-2 {{ bottom: -200px; right: -200px; background: var(--green); }}

        [data-theme="light"] .bg-glow {{ opacity: 0.04; }}

        .container {{ position: relative; z-index: 1; max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}

        /* ---- Theme Toggle ---- */
        .theme-toggle {{
            position: fixed; top: 1.25rem; right: 1.25rem; z-index: 100;
            width: 42px; height: 42px; border-radius: 12px;
            background: var(--bg-secondary); border: 1px solid var(--border);
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            transition: all 0.3s; color: var(--text-secondary);
        }}
        .theme-toggle:hover {{ border-color: var(--accent); color: var(--accent); }}
        .theme-toggle svg {{ width: 18px; height: 18px; }}

        /* ---- Header ---- */
        .header {{ text-align: center; padding: 3rem 0 2rem; }}

        .header-badge {{
            display: inline-flex; align-items: center; gap: 0.5rem;
            padding: 0.4rem 1rem; background: var(--accent-glow);
            border: 1px solid rgba(99, 102, 241, 0.2); border-radius: 100px;
            font-size: 0.75rem; font-weight: 500; color: var(--accent-light);
            letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 1rem;
        }}
        .header-badge .pulse {{
            width: 6px; height: 6px; background: var(--green); border-radius: 50%;
            animation: pulse 2s ease-in-out infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }}
            50% {{ opacity: 0.7; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }}
        }}

        .header h1 {{
            font-size: 2.5rem; font-weight: 800; letter-spacing: -0.03em;
            background: linear-gradient(135deg, var(--text-primary) 0%, var(--accent-light) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
        }}
        .header .subtitle {{ color: var(--text-secondary); margin-top: 0.5rem; font-size: 0.95rem; font-weight: 300; }}

        /* ---- Stats Grid ---- */
        .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.5rem; }}
        .stat {{
            background: var(--bg-secondary); border: 1px solid var(--border);
            border-radius: 16px; padding: 1.25rem; text-align: center; transition: border-color 0.3s;
        }}
        .stat:hover {{ border-color: var(--accent); }}
        .stat-number {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
        .stat-number.accent {{ color: var(--accent-light); }}
        .stat-number.green {{ color: var(--green); }}
        .stat-number.amber {{ color: var(--amber); }}
        .stat-number.cyan {{ color: var(--cyan); }}
        .stat-label {{ font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-top: 0.5rem; }}

        /* ---- Salary Insights Panel ---- */
        .insights-panel {{
            background: var(--bg-secondary); border: 1px solid var(--border);
            border-radius: 16px; padding: 1.5rem; margin-bottom: 1.5rem;
        }}
        .insights-panel h3 {{
            font-size: 0.85rem; font-weight: 600; color: var(--text-secondary);
            text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 1rem;
        }}
        .insights-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }}
        .insight-item {{ text-align: center; }}
        .insight-value {{ font-size: 1.3rem; font-weight: 700; color: var(--green); }}
        .insight-label {{ font-size: 0.7rem; color: var(--text-muted); margin-top: 0.25rem; text-transform: uppercase; letter-spacing: 0.05em; }}

        /* ---- Trend Chart ---- */
        .trend-panel {{
            background: var(--bg-secondary); border: 1px solid var(--border);
            border-radius: 16px; padding: 1.5rem; margin-bottom: 1.5rem;
        }}
        .trend-panel h3 {{
            font-size: 0.85rem; font-weight: 600; color: var(--text-secondary);
            text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 1rem;
        }}
        .trend-chart {{ width: 100%; height: 120px; }}
        .trend-bar {{ fill: var(--accent); rx: 4; transition: fill 0.2s; }}
        .trend-bar:hover {{ fill: var(--accent-light); }}
        .trend-bar-new {{ fill: var(--green); rx: 4; }}
        .trend-label {{ fill: var(--text-muted); font-size: 10px; font-family: 'Inter', sans-serif; }}
        .trend-value {{ fill: var(--text-secondary); font-size: 10px; font-family: 'Inter', sans-serif; }}

        /* ---- Controls ---- */
        .controls {{ display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }}
        .search-box {{ flex: 1; min-width: 200px; position: relative; }}
        .search-box input {{
            width: 100%; padding: 0.75rem 1rem 0.75rem 2.5rem;
            background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px;
            color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 0.9rem;
            outline: none; transition: border-color 0.3s;
        }}
        .search-box input:focus {{ border-color: var(--accent); }}
        .search-box input::placeholder {{ color: var(--text-muted); }}
        .search-box svg {{ position: absolute; left: 0.85rem; top: 50%; transform: translateY(-50%); width: 16px; height: 16px; color: var(--text-muted); }}

        .filter-group {{
            display: flex; gap: 0.35rem; background: var(--bg-secondary);
            border: 1px solid var(--border); border-radius: 12px; padding: 0.25rem;
        }}
        .filter-btn {{
            padding: 0.5rem 1rem; border-radius: 10px; border: none;
            background: transparent; color: var(--text-secondary); cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 0.8rem; font-weight: 500; transition: all 0.2s;
        }}
        .filter-btn:hover {{ color: var(--text-primary); }}
        .filter-btn.active {{ background: var(--accent); color: white; box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3); }}

        .sort-select {{
            padding: 0.65rem 1.25rem 0.65rem 1rem; background: var(--bg-secondary);
            border: 1px solid var(--border); border-radius: 12px; color: var(--text-primary);
            font-family: 'Inter', sans-serif; font-size: 0.8rem; font-weight: 500;
            outline: none; cursor: pointer; appearance: none; -webkit-appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%23a0a0be' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10l-5 5z'/%3E%3C/svg%3E");
            background-repeat: no-repeat; background-position: right 0.75rem center;
            padding-right: 2.25rem; transition: border-color 0.3s;
        }}
        .sort-select:hover {{ border-color: var(--accent); }}
        .sort-select:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
        .sort-select option {{ background: var(--bg-secondary); color: var(--text-primary); }}

        /* ---- Action Buttons Row ---- */
        .action-bar {{
            display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center;
        }}
        .action-btn {{
            display: inline-flex; align-items: center; gap: 0.4rem;
            padding: 0.5rem 1rem; background: var(--bg-secondary);
            border: 1px solid var(--border); border-radius: 10px;
            color: var(--text-secondary); cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 0.8rem; font-weight: 500;
            transition: all 0.2s; text-decoration: none;
        }}
        .action-btn:hover {{ border-color: var(--accent); color: var(--accent-light); }}
        .action-btn svg {{ width: 14px; height: 14px; }}
        .action-btn.active {{ background: var(--accent-glow); border-color: var(--accent); color: var(--accent-light); }}

        /* ---- Results bar ---- */
        .results-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; padding: 0 0.25rem; }}
        .results-count {{ font-size: 0.85rem; color: var(--text-secondary); }}
        .results-count strong {{ color: var(--text-primary); }}

        /* ---- Job Cards ---- */
        .job-list {{ display: grid; gap: 0.75rem; }}

        .job-card {{
            background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px;
            padding: 1.5rem; transition: all 0.25s ease; cursor: default;
            position: relative; overflow: hidden;
        }}
        .job-card::before {{
            content: ''; position: absolute; top: 0; left: 0;
            width: 3px; height: 100%; background: var(--accent); opacity: 0; transition: opacity 0.3s;
        }}
        .job-card:hover {{
            background: var(--bg-card-hover); border-color: rgba(99, 102, 241, 0.3);
            transform: translateY(-1px); box-shadow: 0 4px 24px rgba(0, 0, 0, 0.15);
        }}
        .job-card:hover::before {{ opacity: 1; }}
        .job-card.new::before {{ background: var(--green); opacity: 1; }}
        .job-card.status-applied {{ opacity: 0.6; }}
        .job-card.status-applied::before {{ background: var(--cyan); opacity: 1; }}
        .job-card.status-rejected {{ opacity: 0.35; }}
        .job-card.status-saved::before {{ background: var(--amber); opacity: 1; }}

        .job-card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }}
        .job-title {{ font-size: 1.05rem; font-weight: 600; color: var(--text-primary); line-height: 1.3; }}
        .job-company {{ font-size: 0.9rem; color: var(--accent-light); font-weight: 500; margin-top: 0.2rem; }}
        .job-salary {{ font-size: 0.9rem; font-weight: 600; color: var(--green); white-space: nowrap; }}
        .job-salary.unknown {{ color: var(--text-muted); font-weight: 400; font-size: 0.8rem; }}

        .job-meta {{ display: flex; gap: 1.25rem; margin-top: 0.6rem; flex-wrap: wrap; }}
        .job-meta-item {{ display: flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: var(--text-secondary); }}
        .job-meta-item svg {{ width: 14px; height: 14px; opacity: 0.5; }}

        /* Company research links */
        .company-links {{ display: flex; gap: 0.5rem; margin-top: 0.4rem; }}
        .company-link {{
            font-size: 0.7rem; color: var(--text-muted); text-decoration: none;
            padding: 0.15rem 0.5rem; border-radius: 4px; border: 1px solid var(--border);
            transition: all 0.2s;
        }}
        .company-link:hover {{ color: var(--accent-light); border-color: var(--accent); }}

        .job-desc {{
            font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.75rem; line-height: 1.6;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
        }}

        /* CV Match bar */
        .cv-match {{ margin-top: 0.5rem; display: flex; align-items: center; gap: 0.5rem; }}
        .cv-bar-bg {{ flex: 1; max-width: 120px; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }}
        .cv-bar {{ height: 100%; border-radius: 2px; transition: width 0.5s ease; }}
        .cv-bar.high {{ background: var(--green); }}
        .cv-bar.med {{ background: var(--amber); }}
        .cv-bar.low {{ background: var(--rose); }}
        .cv-label {{ font-size: 0.7rem; color: var(--text-muted); }}

        /* Deadline badge */
        .deadline-badge {{
            font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px;
            font-weight: 600; white-space: nowrap;
        }}
        .deadline-urgent {{ background: var(--rose-glow); color: var(--rose); }}
        .deadline-soon {{ background: var(--amber-glow); color: var(--amber); }}
        .deadline-ok {{ background: var(--green-glow); color: var(--green); }}

        .job-footer {{ display: flex; justify-content: space-between; align-items: center; margin-top: 1rem; flex-wrap: wrap; gap: 0.5rem; }}
        .job-tags {{ display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }}

        .tag {{ font-size: 0.7rem; padding: 0.25rem 0.65rem; border-radius: 6px; font-weight: 500; letter-spacing: 0.02em; }}
        .tag-source {{ background: var(--accent-glow); color: var(--accent-light); border: 1px solid rgba(99, 102, 241, 0.15); }}
        .tag-new {{ background: var(--green-glow); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.15); animation: fadeIn 0.5s ease; }}
        .tag-type {{ background: var(--amber-glow); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.15); }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: scale(0.9); }} to {{ opacity: 1; transform: scale(1); }} }}

        /* Status buttons */
        .status-btns {{ display: flex; gap: 0.3rem; }}
        .status-btn {{
            padding: 0.35rem 0.7rem; border-radius: 8px; border: 1px solid var(--border);
            background: transparent; color: var(--text-muted); cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 0.7rem; font-weight: 500; transition: all 0.2s;
        }}
        .status-btn:hover {{ border-color: var(--accent); color: var(--text-primary); }}
        .status-btn.saved {{ background: var(--amber-glow); border-color: var(--amber); color: var(--amber); }}
        .status-btn.applied {{ background: rgba(34, 211, 238, 0.12); border-color: var(--cyan); color: var(--cyan); }}
        .status-btn.rejected {{ background: var(--rose-glow); border-color: var(--rose); color: var(--rose); }}

        .apply-btn {{
            display: inline-flex; align-items: center; gap: 0.4rem;
            padding: 0.5rem 1.25rem; background: var(--accent); color: white;
            text-decoration: none; border-radius: 10px; font-size: 0.8rem;
            font-weight: 600; font-family: 'Inter', sans-serif; transition: all 0.2s; white-space: nowrap;
        }}
        .apply-btn:hover {{ background: var(--accent-light); box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3); transform: translateY(-1px); }}
        .apply-btn svg {{ width: 14px; height: 14px; }}

        /* ---- Map ---- */
        .location-row {{ display: flex; align-items: center; gap: 0.35rem; }}
        .map-toggle {{
            display: inline-flex; align-items: center; justify-content: center;
            width: 22px; height: 22px; border-radius: 6px; border: 1px solid var(--border);
            background: transparent; color: var(--text-muted); cursor: pointer;
            transition: all 0.2s; flex-shrink: 0; padding: 0;
        }}
        .map-toggle:hover {{ border-color: var(--accent); color: var(--accent-light); background: var(--accent-glow); }}
        .map-toggle svg {{ width: 13px; height: 13px; }}
        .map-container {{
            margin-top: 0.75rem; border-radius: 12px; overflow: hidden;
            border: 1px solid var(--border); height: 0; opacity: 0;
            transition: height 0.35s ease, opacity 0.25s ease;
        }}
        .map-container.open {{ height: 200px; opacity: 1; }}
        .map-container iframe {{ width: 100%; height: 100%; border: 0; }}
        .map-link {{
            font-size: 0.7rem; color: var(--text-muted); text-decoration: none;
            transition: color 0.2s;
        }}
        .map-link:hover {{ color: var(--accent-light); }}

        /* ---- Responsive ---- */
        @media (max-width: 768px) {{
            .stats {{ grid-template-columns: repeat(2, 1fr); }}
            .insights-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .controls {{ flex-direction: column; }}
            .search-box {{ min-width: 100%; }}
            .header h1 {{ font-size: 1.75rem; }}
            .job-card-top {{ flex-direction: column; gap: 0.25rem; }}
        }}

        ::-webkit-scrollbar {{ width: 6px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg-primary); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}

        .page-footer {{ text-align: center; padding: 3rem 0 2rem; color: var(--text-muted); font-size: 0.8rem; }}
        .page-footer a {{ color: var(--accent-light); text-decoration: none; }}

        /* ---- Toast notification ---- */
        .toast {{
            position: fixed; bottom: 2rem; right: 2rem; padding: 0.75rem 1.25rem;
            background: var(--bg-card); border: 1px solid var(--accent); border-radius: 12px;
            color: var(--text-primary); font-size: 0.85rem; z-index: 200;
            transform: translateY(100px); opacity: 0; transition: all 0.3s ease;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }}
        .toast.show {{ transform: translateY(0); opacity: 1; }}
    </style>
</head>
<body>
    <div class="bg-grid"></div>
    <div class="bg-glow bg-glow-1"></div>
    <div class="bg-glow bg-glow-2"></div>

    <!-- Theme Toggle -->
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
        <svg id="themeIcon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"/>
        </svg>
    </button>

    <div class="container">
        <div class="header">
            <div class="header-badge"><span class="pulse"></span>Auto-scanning London jobs</div>
            <h1>London Job Hunter</h1>
            <p class="subtitle">Last scan: {now}</p>
        </div>

        <!-- Stats -->
        <div class="stats">
            <div class="stat"><div class="stat-number accent">{total_count}</div><div class="stat-label">Total Jobs</div></div>
            <div class="stat"><div class="stat-number green">{new_count}</div><div class="stat-label">New This Run</div></div>
            <div class="stat"><div class="stat-number amber">{len(companies)}</div><div class="stat-label">Companies</div></div>
            <div class="stat"><div class="stat-number cyan" id="appliedCount">0</div><div class="stat-label">Applied</div></div>
        </div>

        <!-- Salary Insights -->
        <div class="insights-panel">
            <h3>Salary Insights</h3>
            <div class="insights-grid">
                <div class="insight-item"><div class="insight-value">{"&#163;{:,}".format(salary_stats["avg"]) if salary_stats["avg"] else "N/A"}</div><div class="insight-label">Average</div></div>
                <div class="insight-item"><div class="insight-value">{"&#163;{:,}".format(salary_stats["median"]) if salary_stats["median"] else "N/A"}</div><div class="insight-label">Median</div></div>
                <div class="insight-item"><div class="insight-value">{"&#163;{:,}".format(salary_stats["min"]) if salary_stats["min"] else "N/A"}</div><div class="insight-label">Lowest</div></div>
                <div class="insight-item"><div class="insight-value">{"&#163;{:,}".format(salary_stats["max"]) if salary_stats["max"] else "N/A"}</div><div class="insight-label">Highest</div></div>
            </div>
        </div>

        <!-- Trend Chart -->
        {"" if len(history) < 2 else '<div class="trend-panel"><h3>Job Trends</h3><canvas id="trendCanvas" class="trend-chart"></canvas></div>'}

        <!-- Controls -->
        <div class="controls">
            <div class="search-box">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                <input type="text" id="searchInput" placeholder="Search jobs, companies, keywords..." oninput="applyFilters()">
            </div>
            <div class="filter-group">
                <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
                <button class="filter-btn" onclick="setFilter('new', this)">New</button>
                <button class="filter-btn" onclick="setFilter('saved', this)">Saved</button>
                <button class="filter-btn" onclick="setFilter('applied', this)">Applied</button>
                <button class="filter-btn" onclick="setFilter('Adzuna', this)">Adzuna</button>
                <button class="filter-btn" onclick="setFilter('Reed', this)">Reed</button>
                {"<button class='filter-btn' onclick=" + "'setFilter(" + '"Jooble"' + ", this)'" + ">Jooble</button>" if JOOBLE_API_KEY else ""}
                {"<button class='filter-btn' onclick=" + "'setFilter(" + '"Findwork"' + ", this)'" + ">Findwork</button>" if FINDWORK_API_KEY else ""}
                <button class="filter-btn" onclick="setFilter('The Muse', this)">The Muse</button>
                <button class="filter-btn" onclick="setFilter('Jobicy', this)">Jobicy</button>
            </div>
            <select class="sort-select" id="sortSelect" onchange="applyFilters()">
                <option value="date">Newest first</option>
                <option value="salary-high">Salary: High to Low</option>
                <option value="salary-low">Salary: Low to High</option>
                <option value="company">Company A-Z</option>
                {"<option value='cv-match'>Best CV Match</option>" if has_cv else ""}
                <option value="deadline">Closing Soon</option>
            </select>
        </div>

        <!-- Action Buttons -->
        <div class="action-bar">
            <button class="action-btn" onclick="exportCSV()">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>
                Export CSV
            </button>
            <button class="action-btn" onclick="toggleHideApplied()" id="hideAppliedBtn">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"/></svg>
                Hide Applied
            </button>
            {"<button class='action-btn' id='cvUploadBtn' onclick=" + '"' + "document.getElementById('cvInput').click()" + '"' + ">Upload CV for Matching</button><input type='file' id='cvInput' accept='.txt,.pdf,.doc,.docx' style='display:none' onchange='handleCVUpload(event)'>" if not has_cv else "<span class='action-btn active'>CV Matched</span>"}
        </div>

        <div class="results-bar">
            <span class="results-count" id="resultsCount">Showing <strong>{total_count}</strong> jobs</span>
        </div>

        <div class="job-list" id="jobList"></div>

        <div class="page-footer">
            London Job Hunter &middot; Auto-generated report<br>
            Built by Seb Barclay
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
    // ---- Data ----
    const JOBS = {jobs_json_str};
    const TREND_DATA = {trend_data};
    const HAS_CV = {'true' if has_cv else 'false'};

    // ---- Local Storage for job statuses ----
    function getStatuses() {{
        try {{ return JSON.parse(localStorage.getItem('jh_statuses') || '{{}}'); }} catch {{ return {{}}; }}
    }}
    function saveStatuses(s) {{ localStorage.setItem('jh_statuses', JSON.stringify(s)); }}

    function setJobStatus(jobId, status) {{
        const s = getStatuses();
        if (s[jobId] === status) {{ delete s[jobId]; }} else {{ s[jobId] = status; }}
        saveStatuses(s);
        renderJobs();
        updateAppliedCount();
        showToast(s[jobId] ? `Marked as ${{status}}` : 'Status cleared');
    }}

    function updateAppliedCount() {{
        const s = getStatuses();
        const count = Object.values(s).filter(v => v === 'applied').length;
        document.getElementById('appliedCount').textContent = count;
    }}

    // ---- Theme ----
    function toggleTheme() {{
        const html = document.documentElement;
        const current = html.getAttribute('data-theme');
        const next = current === 'light' ? 'dark' : 'light';
        html.setAttribute('data-theme', next);
        localStorage.setItem('jh_theme', next);
        updateThemeIcon(next);
    }}

    function updateThemeIcon(theme) {{
        const icon = document.getElementById('themeIcon');
        if (theme === 'light') {{
            icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
        }} else {{
            icon.innerHTML = '<path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"/>';
        }}
    }}

    // ---- Toast ----
    function showToast(msg) {{
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 2000);
    }}

    // ---- Filtering ----
    let currentFilter = 'all';
    let hideApplied = false;

    function setFilter(filter, btn) {{
        currentFilter = filter;
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyFilters();
    }}

    function toggleHideApplied() {{
        hideApplied = !hideApplied;
        document.getElementById('hideAppliedBtn').classList.toggle('active', hideApplied);
        applyFilters();
    }}

    function applyFilters() {{
        const search = document.getElementById('searchInput').value.toLowerCase();
        const sort = document.getElementById('sortSelect').value;
        const statuses = getStatuses();
        let filtered = JOBS.filter(job => {{
            const text = `${{job.title}} ${{job.company}} ${{job.description}} ${{job.location}}`.toLowerCase();
            const matchesSearch = !search || text.includes(search);
            const status = statuses[job.id] || '';

            let matchesFilter = true;
            if (currentFilter === 'new') matchesFilter = job.is_new;
            else if (currentFilter === 'saved') matchesFilter = status === 'saved';
            else if (currentFilter === 'applied') matchesFilter = status === 'applied';
            else if (currentFilter !== 'all') matchesFilter = job.source === currentFilter;

            if (hideApplied && (status === 'applied' || status === 'rejected')) return false;
            return matchesSearch && matchesFilter;
        }});

        // Sort
        filtered.sort((a, b) => {{
            if (sort === 'date') return (b.date_posted || '').localeCompare(a.date_posted || '');
            if (sort === 'salary-high') return (b.salary_val || 0) - (a.salary_val || 0);
            if (sort === 'salary-low') return (a.salary_val || 0) - (b.salary_val || 0);
            if (sort === 'company') return (a.company || '').localeCompare(b.company || '');
            if (sort === 'cv-match') return (b.cv_score || 0) - (a.cv_score || 0);
            if (sort === 'deadline') {{
                const daysA = daysSincePosted(a.date_posted);
                const daysB = daysSincePosted(b.date_posted);
                return daysB - daysA; // oldest first (closest to expiry)
            }}
            return 0;
        }});

        renderJobList(filtered);
        document.getElementById('resultsCount').innerHTML = `Showing <strong>${{filtered.length}}</strong> jobs`;
    }}

    // ---- Deadline calculation ----
    function daysSincePosted(dateStr) {{
        if (!dateStr) return 0;
        const posted = new Date(dateStr);
        const now = new Date();
        return Math.floor((now - posted) / (1000 * 60 * 60 * 24));
    }}

    function deadlineBadge(dateStr) {{
        const days = daysSincePosted(dateStr);
        const remaining = 30 - days;
        if (remaining <= 3) return `<span class="deadline-badge deadline-urgent">Closes in ~${{Math.max(0, remaining)}}d</span>`;
        if (remaining <= 10) return `<span class="deadline-badge deadline-soon">~${{remaining}}d left</span>`;
        return `<span class="deadline-badge deadline-ok">~${{remaining}}d left</span>`;
    }}

    // ---- CV Match bar ----
    function cvMatchHTML(score) {{
        if (!HAS_CV || score === 0) return '';
        const cls = score >= 60 ? 'high' : score >= 30 ? 'med' : 'low';
        return `<div class="cv-match"><div class="cv-bar-bg"><div class="cv-bar ${{cls}}" style="width:${{score}}%"></div></div><span class="cv-label">${{score}}% CV match</span></div>`;
    }}

    // ---- Render ----
    function renderJobList(jobs) {{
        const statuses = getStatuses();
        const list = document.getElementById('jobList');
        list.innerHTML = jobs.map(job => {{
            const status = statuses[job.id] || '';
            const statusClass = status ? `status-${{status}}` : '';
            const newClass = job.is_new ? 'new' : '';
            const salaryHTML = job.salary_str
                ? `<span class="job-salary">${{job.salary_str}}</span>`
                : '<span class="job-salary unknown">Salary unlisted</span>';

            const companyEncoded = encodeURIComponent(job.company);
            const locationEncoded = encodeURIComponent(job.location + ', London, UK');
            const mapsSearchUrl = `https://www.google.com/maps/search/?api=1&query=${{locationEncoded}}`;
            const mapsEmbedQuery = encodeURIComponent(job.company + ', ' + job.location + ', London, UK');
            const glassdoorUrl = `https://www.glassdoor.co.uk/Reviews/${{companyEncoded}}-Reviews-E_IE0.htm`;
            const linkedinUrl = `https://www.linkedin.com/company/${{job.company_slug}}`;
            const googleUrl = `https://www.google.com/search?q=${{companyEncoded}}+company`;

            return `
            <div class="job-card ${{newClass}} ${{statusClass}}" data-id="${{job.id}}">
                <div class="job-card-top">
                    <div>
                        <div class="job-title">${{escapeHTML(job.title)}}</div>
                        <div class="job-company">${{escapeHTML(job.company)}}</div>
                        <div class="company-links">
                            <a href="${{glassdoorUrl}}" target="_blank" class="company-link">Glassdoor</a>
                            <a href="${{linkedinUrl}}" target="_blank" class="company-link">LinkedIn</a>
                            <a href="${{googleUrl}}" target="_blank" class="company-link">Google</a>
                        </div>
                    </div>
                    ${{salaryHTML}}
                </div>
                <div class="job-meta">
                    <span class="job-meta-item location-row">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/><path d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z"/></svg>
                        ${{job.location}}
                        <button class="map-toggle" onclick="toggleMap('${{job.id}}')" title="Show on map">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 6.75V15m6-6v8.25m.503 3.498l4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 00-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0z"/></svg>
                        </button>
                        <a href="${{mapsSearchUrl}}" target="_blank" class="map-link" title="Open in Google Maps">Maps</a>
                    </span>
                    <span class="job-meta-item">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"/></svg>
                        ${{job.date_posted}}
                    </span>
                    ${{deadlineBadge(job.date_posted)}}
                </div>
                <div class="map-container" id="map-${{job.id}}">
                    <iframe loading="lazy" referrerpolicy="no-referrer-when-downgrade"
                        src="" data-src="https://maps.google.com/maps?q=${{mapsEmbedQuery}}&t=&z=14&ie=UTF8&iwloc=&output=embed">
                    </iframe>
                </div>
                ${{cvMatchHTML(job.cv_score)}}
                <div class="job-desc">${{escapeHTML(job.description)}}</div>
                <div class="job-footer">
                    <div class="job-tags">
                        <span class="tag tag-source">${{job.source}}</span>
                        ${{job.is_new ? '<span class="tag tag-new">NEW</span>' : ''}}
                        ${{job.contract_type ? `<span class="tag tag-type">${{job.contract_type}}</span>` : ''}}
                        <div class="status-btns">
                            <button class="status-btn ${{status === 'saved' ? 'saved' : ''}}" onclick="setJobStatus('${{job.id}}', 'saved')">Save</button>
                            <button class="status-btn ${{status === 'applied' ? 'applied' : ''}}" onclick="setJobStatus('${{job.id}}', 'applied')">Applied</button>
                            <button class="status-btn ${{status === 'rejected' ? 'rejected' : ''}}" onclick="setJobStatus('${{job.id}}', 'rejected')">Not Interested</button>
                        </div>
                    </div>
                    <a href="${{job.url}}" target="_blank" class="apply-btn" title="${{getDomain(job.url)}}">
                        Apply on ${{getDomain(job.url)}}
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/></svg>
                    </a>
                </div>
            </div>`;
        }}).join('');
    }}

    function renderJobs() {{ applyFilters(); }}

    // ---- Map toggle ----
    function toggleMap(jobId) {{
        const container = document.getElementById(`map-${{jobId}}`);
        if (!container) return;
        const isOpen = container.classList.contains('open');
        // Close all other maps first
        document.querySelectorAll('.map-container.open').forEach(el => {{
            el.classList.remove('open');
        }});
        if (!isOpen) {{
            // Lazy-load the iframe src on first open
            const iframe = container.querySelector('iframe');
            if (iframe && !iframe.src.includes('google.com/maps')) {{
                iframe.src = iframe.getAttribute('data-src');
            }}
            container.classList.add('open');
        }}
    }}

    function escapeHTML(str) {{
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }}

    function getDomain(url) {{
        try {{
            const hostname = new URL(url).hostname.replace('www.', '');
            // Prettify common job sites
            const names = {{
                'reed.co.uk': 'Reed',
                'indeed.co.uk': 'Indeed',
                'indeed.com': 'Indeed',
                'linkedin.com': 'LinkedIn',
                'glassdoor.co.uk': 'Glassdoor',
                'glassdoor.com': 'Glassdoor',
                'totaljobs.com': 'Totaljobs',
                'cwjobs.co.uk': 'CWJobs',
                'monster.co.uk': 'Monster',
                'jooble.org': 'Jooble',
                'findwork.dev': 'Findwork',
                'myworkdayjobs.com': 'Workday',
                'lever.co': 'Lever',
                'greenhouse.io': 'Greenhouse',
                'smartrecruiters.com': 'SmartRecruiters',
                'jobs.lever.co': 'Lever',
                'boards.greenhouse.io': 'Greenhouse',
                'apply.workable.com': 'Workable',
            }};
            // Check for partial matches (e.g., *.myworkdayjobs.com)
            for (const [domain, name] of Object.entries(names)) {{
                if (hostname.endsWith(domain)) return name;
            }}
            // Return shortened domain
            const parts = hostname.split('.');
            return parts.length > 2 ? parts.slice(-2).join('.') : hostname;
        }} catch {{
            return 'site';
        }}
    }}

    // ---- CSV Export ----
    function exportCSV() {{
        const statuses = getStatuses();
        const NL = String.fromCharCode(10);
        let csv = 'Title,Company,Location,Salary,Date Posted,Source,Status,URL' + NL;
        JOBS.forEach(j => {{
            const status = statuses[j.id] || 'new';
            const clean = s => (s||'').replace(/"/g, "'");
            csv += '"' + clean(j.title) + '","' + clean(j.company) + '","' + j.location + '","' + j.salary_str + '","' + j.date_posted + '","' + j.source + '","' + status + '","' + j.url + '"' + NL;
        }});
        const blob = new Blob([csv], {{ type: 'text/csv' }});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `london-jobs-${{new Date().toISOString().slice(0,10)}}.csv`;
        a.click();
        showToast('CSV downloaded!');
    }}

    // ---- CV Upload (client-side text extraction) ----
    function handleCVUpload(event) {{
        const file = event.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function(e) {{
            const text = e.target.result.toLowerCase();
            const re = new RegExp('[a-z]{{3,}}', 'g');
            const words = text.match(re) || [];
            const stopWords = new Set(['the','and','for','are','but','not','you','all','can','had','was','one','our','has','have','been','will','with','this','that','from','they','were','which','their','would','there','about','into','more','other','than','them','these','some','also','what','when','where']);
            const counts = {{}};
            words.forEach(w => {{ if (!stopWords.has(w)) counts[w] = (counts[w]||0) + 1; }});
            const keywords = Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0,80).map(e => e[0]);
            localStorage.setItem('jh_cv_keywords', JSON.stringify(keywords));
            // Re-score jobs
            JOBS.forEach(j => {{
                const jtext = `${{j.title}} ${{j.description}}`.toLowerCase();
                const matches = keywords.filter(kw => jtext.includes(kw)).length;
                j.cv_score = Math.min(100, Math.floor((matches / keywords.length) * 300));
            }});
            showToast(`CV loaded! ${{keywords.length}} keywords extracted`);
            applyFilters();
        }};
        reader.readAsText(file);
    }}

    // ---- Trend Chart (Canvas) ----
    function drawTrend() {{
        const canvas = document.getElementById('trendCanvas');
        if (!canvas || TREND_DATA.length < 2) return;
        const ctx = canvas.getContext('2d');
        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = 120;
        const w = canvas.width;
        const h = canvas.height;
        const padding = {{ top: 15, bottom: 25, left: 10, right: 10 }};
        const chartW = w - padding.left - padding.right;
        const chartH = h - padding.top - padding.bottom;
        const maxVal = Math.max(...TREND_DATA.map(d => d.total), 1);
        const barW = Math.max(8, Math.min(40, chartW / TREND_DATA.length - 4));
        const gap = (chartW - barW * TREND_DATA.length) / (TREND_DATA.length + 1);

        // Clear
        ctx.clearRect(0, 0, w, h);

        const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
        const green = getComputedStyle(document.documentElement).getPropertyValue('--green').trim();
        const muted = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim();

        TREND_DATA.forEach((d, i) => {{
            const x = padding.left + gap + i * (barW + gap);
            const barH = (d.total / maxVal) * chartH;
            const y = padding.top + chartH - barH;

            // Total bar
            ctx.fillStyle = accent;
            ctx.beginPath();
            ctx.roundRect(x, y, barW, barH, 3);
            ctx.fill();

            // New jobs overlay
            if (d.new > 0) {{
                const newH = (d.new / maxVal) * chartH;
                ctx.fillStyle = green;
                ctx.beginPath();
                ctx.roundRect(x, padding.top + chartH - newH, barW, newH, 3);
                ctx.fill();
            }}

            // Value label
            ctx.fillStyle = muted;
            ctx.font = '10px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(d.total, x + barW/2, y - 4);

            // Date label
            ctx.fillText(d.date.slice(5), x + barW/2, h - 4);
        }});
    }}

    // ---- Init ----
    (function init() {{
        // Theme
        const savedTheme = localStorage.getItem('jh_theme') || 'dark';
        document.documentElement.setAttribute('data-theme', savedTheme);
        updateThemeIcon(savedTheme);

        // Load CV keywords from localStorage if uploaded in browser
        const savedCVKeywords = localStorage.getItem('jh_cv_keywords');
        if (savedCVKeywords && !HAS_CV) {{
            try {{
                const keywords = JSON.parse(savedCVKeywords);
                JOBS.forEach(j => {{
                    const jtext = `${{j.title}} ${{j.description}}`.toLowerCase();
                    const matches = keywords.filter(kw => jtext.includes(kw)).length;
                    j.cv_score = Math.min(100, Math.floor((matches / keywords.length) * 300));
                }});
            }} catch {{}}
        }}

        updateAppliedCount();
        renderJobs();
        drawTrend();
        window.addEventListener('resize', drawTrend);
    }})();
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
    if not EMAIL_ENABLED or not new_jobs:
        return
    if not EMAIL_PASSWORD:
        print("  Email skipped (no app password set in config.py)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"London Job Hunter: {len(new_jobs)} new jobs found!"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    text_body = f"Found {len(new_jobs)} new jobs:\n\n"
    html_body = f"""<h2 style="color:#6366f1;">London Job Hunter - {len(new_jobs)} New Jobs</h2><hr>"""

    for job in new_jobs:
        salary_str = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"\u00a3{int(job['salary_min']):,} - \u00a3{int(job['salary_max']):,}"

        text_body += f"- {job['title']} at {job['company']} ({salary_str})\n  {job['url']}\n\n"
        html_body += f"""
        <div style="margin:1rem 0;padding:1rem;border-left:3px solid #6366f1;background:#f8fafc;">
            <strong>{job['title']}</strong><br>
            <span style="color:#6366f1;">{job['company']}</span> | {job.get('location','')} | {salary_str}<br>
            <p style="color:#64748b;font-size:0.9rem;">{job.get('description','')[:200]}</p>
            <a href="{job['url']}" style="color:#6366f1;">View & Apply</a>
        </div>
        """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=ssl.create_default_context(cafile=certifi.where()))
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"  Email sent to {EMAIL_RECIPIENT}")
    except Exception as e:
        print(f"  Email failed: {e}")


# ============================================================
# Main
# ============================================================

def run():
    print("=" * 55)
    print("  LONDON JOB HUNTER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    db = load_database()
    all_new_jobs = []
    new_job_ids = set()

    for jid in db["jobs"]:
        db["jobs"][jid]["seen_this_run"] = False

    total_roles = len(SEARCH_ROLES)
    for i, role in enumerate(SEARCH_ROLES, 1):
        print(f"\n[{i}/{total_roles}] Searching: {role}")

        adzuna_jobs = search_adzuna(role)
        reed_jobs = search_reed(role)
        jooble_jobs = search_jooble(role)
        findwork_jobs = search_findwork(role)
        muse_jobs = search_themuse(role)
        jobicy_jobs = search_jobicy(role)
        combined = adzuna_jobs + reed_jobs + jooble_jobs + findwork_jobs + muse_jobs + jobicy_jobs

        counts = []
        if adzuna_jobs: counts.append(f"{len(adzuna_jobs)} Adzuna")
        if reed_jobs: counts.append(f"{len(reed_jobs)} Reed")
        if jooble_jobs: counts.append(f"{len(jooble_jobs)} Jooble")
        if findwork_jobs: counts.append(f"{len(findwork_jobs)} Findwork")
        if muse_jobs: counts.append(f"{len(muse_jobs)} Muse")
        if jobicy_jobs: counts.append(f"{len(jobicy_jobs)} Jobicy")
        print(f"  Found {' + '.join(counts) or '0'} = {len(combined)} results")

        for job in combined:
            if is_excluded(job):
                continue
            if is_duplicate(job, db["jobs"]):
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

        time.sleep(1)

    # Expire old jobs
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

    for jid in db["jobs"]:
        db["jobs"][jid].pop("seen_this_run", None)

    db["last_run"] = datetime.now().isoformat()
    db["history"].append({
        "date": datetime.now().isoformat(),
        "new_jobs": len(all_new_jobs),
        "total_jobs": len(db["jobs"]),
        "expired": len(expired),
    })
    db["history"] = db["history"][-50:]

    # Validate URLs - remove jobs with dead links
    dead_links = validate_and_clean_urls(db)

    save_database(db)

    print(f"\n{'=' * 55}")
    print(f"  RESULTS SUMMARY")
    print(f"  New jobs found:  {len(all_new_jobs)}")
    print(f"  Total active:    {len(db['jobs'])}")
    print(f"  Expired/removed: {len(expired)}")
    print(f"  Dead links:      {len(dead_links)}")
    print(f"{'=' * 55}")

    generate_html_report(db, new_job_ids)
    export_csv(db)

    if EMAIL_ENABLED and all_new_jobs:
        send_email_notification(all_new_jobs)
    elif not EMAIL_ENABLED:
        print("  Email notifications disabled")

    print(f"\n  Done! Open {HTML_REPORT_FILE} in your browser to view jobs.")
    return len(all_new_jobs)


if __name__ == "__main__":
    run()
