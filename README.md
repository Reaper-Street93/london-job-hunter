# London Job Hunter

An automated job search tool that scans multiple job boards for entry-level tech/business roles in London and generates clean, filterable HTML reports.

## What It Does

- Searches **Adzuna** and **Reed** APIs for roles like Solutions Engineer, Business Analyst, Data Analyst, Product Manager, and more
- Tracks jobs across runs — flags new listings and removes expired ones
- Generates a **dark-themed HTML dashboard** with filtering (by source, new jobs)
- Optionally sends **email notifications** when new jobs appear
- Runs on a schedule (twice weekly via cron)

## Target Roles

Focused on entry-level positions that bridge **tech and people/business**:

- Solutions Engineer / Technical Consultant
- Business Analyst / Systems Analyst
- Data Analyst / Product Analyst
- Product Manager / Project Manager
- Customer Success Manager / Technical Account Manager
- Implementation Specialist / Integration Specialist
- Pre-Sales Consultant

## Setup

### 1. Get API Keys (Free)

| Service | Sign Up | What You Get |
|---------|---------|-------------|
| **Adzuna** | [developer.adzuna.com](https://developer.adzuna.com/) | App ID + App Key |
| **Reed** | [reed.co.uk/developers](https://www.reed.co.uk/developers/jobseeker) | API Key |

### 2. Configure

```bash
cp config.example.py config.py
# Edit config.py with your API keys
```

### 3. Run

```bash
python3 job_hunter.py
```

### 4. View Results

Open `reports/latest_jobs.html` in your browser.

### 5. Automate (Optional)

The included `setup_cron.sh` script sets up twice-weekly runs:

```bash
chmod +x setup_cron.sh
./setup_cron.sh
```

This runs the hunter every **Wednesday and Saturday at 9am**.

## Email Notifications (Optional)

To get email alerts when new jobs are found:

1. Enable 2FA on your Gmail account
2. Create an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Update `config.py`:
   ```python
   EMAIL_ENABLED = True
   EMAIL_SENDER = "you@gmail.com"
   EMAIL_PASSWORD = "your-app-password"
   EMAIL_RECIPIENT = "you@gmail.com"
   ```

## Project Structure

```
london-job-hunter/
├── job_hunter.py        # Main script - searches APIs, generates reports
├── config.py            # Your API keys and settings (git-ignored)
├── config.example.py    # Template config for others to use
├── setup_cron.sh        # Cron job installer
├── jobs_database.json   # Job tracking database (git-ignored)
├── reports/
│   └── latest_jobs.html # Generated HTML dashboard
└── README.md
```

## Tech Stack

- **Python 3** (stdlib only — no pip dependencies)
- **Adzuna API** + **Reed API** for job data
- **HTML/CSS/JS** for the report dashboard

## Built With

Built as a portfolio project to demonstrate:
- API integration and data aggregation
- Automation and scheduling
- Clean report generation
- Python scripting without external dependencies
