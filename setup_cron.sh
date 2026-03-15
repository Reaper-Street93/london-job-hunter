#!/bin/bash
# ============================================================
# London Job Hunter - Cron Job Setup
# Runs the job hunter every Wednesday and Saturday at 9am
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_PATH="$(which python3)"
CRON_CMD="0 9 * * 3,6 cd $SCRIPT_DIR && $PYTHON_PATH job_hunter.py >> $SCRIPT_DIR/cron.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "london-job-hunter"; then
    echo "Cron job already exists. Updating..."
    crontab -l 2>/dev/null | grep -v "london-job-hunter" | crontab -
fi

# Add the cron job
(crontab -l 2>/dev/null; echo "# london-job-hunter: auto job search"; echo "$CRON_CMD") | crontab -

echo "Cron job installed successfully!"
echo "Schedule: Every Wednesday and Saturday at 9:00 AM"
echo "Command: $CRON_CMD"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -l | grep -v london-job-hunter | crontab -"
