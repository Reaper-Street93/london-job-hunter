# ============================================================
# London Job Hunter - Configuration (EXAMPLE)
# ============================================================
# Copy this file to config.py and fill in your API keys.
#
# Get your free API keys:
#   Adzuna:   https://developer.adzuna.com/
#   Reed:     https://www.reed.co.uk/developers/jobseeker
#   Jooble:   https://jooble.org/api/about
#   Findwork: https://findwork.dev/developers/
# ============================================================

# ---- Adzuna API Credentials ----
ADZUNA_APP_ID = "your_app_id_here"
ADZUNA_APP_KEY = "your_app_key_here"

# ---- Reed API Credentials ----
REED_API_KEY = "your_api_key_here"

# ---- Jooble API Credentials (Optional - aggregates many job boards) ----
JOOBLE_API_KEY = ""

# ---- Findwork API Credentials (Optional - good for tech roles) ----
FINDWORK_API_KEY = ""

# ---- Email Settings (Optional) ----
EMAIL_ENABLED = False
EMAIL_SENDER = "you@gmail.com"
EMAIL_PASSWORD = ""  # Gmail App Password
EMAIL_RECIPIENT = "you@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# ---- Search Settings ----
LOCATION = "London"
MIN_SALARY = 28000
MAX_SALARY = 60000
DISTANCE_MILES = 15
FULL_TIME_ONLY = True

# ---- Roles to Search ----
SEARCH_ROLES = [
    "Junior Solutions Engineer",
    "Graduate Solutions Engineer",
    "Junior Business Analyst",
    "Graduate Business Analyst",
    "Junior Data Analyst",
    "Graduate Data Analyst",
    "Junior Product Analyst",
    "Junior Product Manager",
    "Associate Product Manager",
    "Junior Project Manager",
    "Graduate Project Manager",
    "Junior Technical Account Manager",
    "Junior Customer Success Manager",
    "Graduate Customer Success Manager",
    "Junior Implementation Specialist",
    "Implementation Consultant Graduate",
    "Junior Pre-Sales Consultant",
    "Junior Systems Analyst",
    "Graduate Systems Analyst",
    "Junior Integration Specialist",
    "Technical Support Engineer Graduate",
    "Junior Technical Consultant",
]

# ---- Excluded Keywords (jobs containing these are filtered out) ----
EXCLUDE_KEYWORDS = [
    "senior", "sr.", "sr ", "lead", "principal", "head of", "director",
    "manager of", "staff",
    "recruitment", "recruiter", "recruiting", "talent acquisition",
]

# ---- Output Settings ----
OUTPUT_DIR = "reports"
JOBS_DB_FILE = "jobs_database.json"
HTML_REPORT_FILE = "reports/latest_jobs.html"
