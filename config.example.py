# ============================================================
# London Job Hunter - Configuration (EXAMPLE)
# ============================================================
# Copy this file to config.py and fill in your API keys.
#
# Get your free API keys:
#   Adzuna: https://developer.adzuna.com/
#   Reed:   https://www.reed.co.uk/developers/jobseeker
# ============================================================

# ---- Adzuna API Credentials ----
ADZUNA_APP_ID = "your_app_id_here"
ADZUNA_APP_KEY = "your_app_key_here"

# ---- Reed API Credentials ----
REED_API_KEY = "your_api_key_here"

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

# ---- Output Settings ----
OUTPUT_DIR = "reports"
JOBS_DB_FILE = "jobs_database.json"
HTML_REPORT_FILE = "reports/latest_jobs.html"
