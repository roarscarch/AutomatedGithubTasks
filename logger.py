"""Rotating file logger + weekly JSON report."""
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "automation.log"
REPORT_FILE = ROOT / "report.json"


def setup_logger(name="automator"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=1_048_576, backupCount=5)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    return logger


def log_daily_run(project_name, commits_made, repo_url):
    """Append a daily run entry to the JSON report."""
    records = []
    if REPORT_FILE.exists():
        records = json.loads(REPORT_FILE.read_text())

    records.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().isoformat(),
        "project": project_name,
        "commits": commits_made,
        "repo": repo_url,
    })
    records = records[-365:]
    REPORT_FILE.write_text(json.dumps(records, indent=2))
