#!/usr/bin/env python3
"""
AI-powered daily commit automation — one novel project per week.

Each day, this picks up where it left off:
  - If it's Monday (or no active project), DeepSeek generates a fresh idea,
    a new GitHub repo is created, and work begins.
  - Tuesday through Sunday, commits continue on the same project.
  - 5-10 AI-generated commits are made each day (random count).

Usage:
  python scheduler.py run          # run once immediately
  python scheduler.py daemon       # long-lived scheduler (fires daily)
  python scheduler.py status       # show current project + history
  python scheduler.py install      # print crontab line
"""

import argparse
import json
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ideator import generate_idea
from repoman import create_repo, bootstrap_scaffold
from committer import run as commit_run, load_config
from logger import setup_logger, log_daily_run

log = setup_logger()


# ============================================================================
# State management
# ============================================================================

def load_state():
    with open(ROOT / "state.json") as f:
        return json.load(f)


def save_state(state):
    with open(ROOT / "state.json", "w") as f:
        json.dump(state, f, indent=2)


def is_monday():
    return date.today().weekday() == 0


def weeks_since(d):
    """Return number of whole weeks elapsed since date d."""
    if d is None:
        return 999
    start = date.fromisoformat(d)
    return (date.today() - start).days // 7


# ============================================================================
# Daily job
# ============================================================================

def daily_job():
    """Core logic — decide whether to start a new project or continue."""
    state = load_state()
    today = str(date.today())

    need_new_project = (
        state["current_project"] is None
        or is_monday()
        or weeks_since(state["week_start_date"]) >= 1
    )

    if need_new_project:
        log.info("=" * 60)
        log.info("NEW PROJECT WEEK — generating idea via DeepSeek...")

        idea = generate_idea()
        log.info(f"Idea: {idea['name']} — {idea['tagline']}")
        log.info(f"Stack: {idea['language']} / {', '.join(idea['stack'])}")

        log.info("Creating GitHub repo...")
        repo_path = create_repo(idea)
        config = load_config()
        username = config.get("github_username", "user")
        repo_url = f"https://github.com/{username}/{idea['name']}"
        log.info(f"Repo: {repo_url}")

        log.info("Bootstrapping scaffold...")
        bootstrap_scaffold(repo_path, idea)

        state["current_project"] = idea
        state["week_start_date"] = today
        state["day_in_week"] = 0
        state["total_commits_this_week"] = 0
    else:
        idea = state["current_project"]
        config = load_config()
        work_dir = ROOT / config.get("work_dir", "./repos")
        repo_path = work_dir / idea["name"]
        # In GitHub Actions the filesystem is fresh each run — clone if missing
        if not repo_path.exists():
            log.info(f"Cloning existing project repo: {idea['name']}...")
            from repoman import clone_repo
            repo_path = clone_repo(idea["name"], work_dir)

    # Pull latest
    import subprocess
    subprocess.run(["git", "-C", str(repo_path), "pull", "origin", "main"],
                       capture_output=True)

    # ---- make today's commits ----
    commits_made = commit_run(repo_path, idea)

    # Update state
    state["day_in_week"] = (date.today() - date.fromisoformat(state["week_start_date"])).days
    state["total_commits_this_week"] += commits_made

    # Record in history
    state["history"].append({
        "date": today,
        "project": idea["name"],
        "commits": commits_made,
        "day_in_week": state["day_in_week"],
    })
    state["history"] = state["history"][-365:]
    save_state(state)

    config = load_config()
    username = config.get("github_username", "user")
    repo_url = f"https://github.com/{username}/{idea['name']}"
    log_daily_run(idea["name"], commits_made, repo_url)

    log.info(f"Week day {state['day_in_week']}/6 — {state['total_commits_this_week']} commits so far this week.")


# ============================================================================
# Daemon
# ============================================================================

def daemon():
    """Long-running process that fires daily at the configured time."""
    config = load_config()
    hour = config.get("schedule_hour", 9)
    minute = config.get("schedule_minute", 13)

    log.info(f"Daemon started — daily run at {hour:02d}:{minute:02d}")

    def shutdown(signum, frame):
        log.info("Daemon shutting down.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        wait = (next_run - now).total_seconds()
        log.info(f"Next run at {next_run.isoformat()} ({wait / 60:.0f} min)")
        time.sleep(wait)

        daily_job()
        time.sleep(60)


# ============================================================================
# CLI
# ============================================================================

def cmd_run():
    daily_job()


def cmd_daemon():
    daemon()


def cmd_status():
    state = load_state()
    config = load_config()

    import os
    key_source = "env var" if os.environ.get("DEEPSEEK_API_KEY") else ("config.json" if config.get("deepseek_api_key") else "NOT SET")
    print(f"DeepSeek key:     {key_source}")
    print(f"Commits per day:  {config.get('commits_min', 5)}–{config.get('commits_max', 10)} (random)")
    h, m = config.get("schedule_hour", 9), config.get("schedule_minute", 13)
    print(f"Schedule:         {h:02d}:{m:02d} daily")
    print(f"GitHub user:      {config.get('github_username', 'not set')}")

    proj = state.get("current_project")
    if proj:
        print(f"\nCurrent project:  {proj['name']}")
        print(f"  Tagline:        {proj['tagline']}")
        print(f"  Language:       {proj['language']}")
        print(f"  Week started:   {state['week_start_date']} (day {state['day_in_week']}/6)")
        print(f"  Commits so far: {state['total_commits_this_week']}")
        username = config.get("github_username", "user")
        print(f"  Repo:           https://github.com/{username}/{proj['name']}")
    else:
        print("\nNo active project — next run will generate one.")

    print("\nRecent history (last 14):")
    for entry in state["history"][-14:]:
        print(f"  {entry['date']} | {entry['project']} | {entry['commits']} commits | day {entry.get('day_in_week', '?')}")

    report_file = ROOT / "report.json"
    if report_file.exists():
        records = json.loads(report_file.read_text())
        total = sum(r["commits"] for r in records)
        print(f"\n{len(records)} daily runs, {total} total commits across all projects.")


def cmd_install():
    config = load_config()
    hour = config.get("schedule_hour", 9)
    minute = config.get("schedule_minute", 13)
    py = sys.executable
    script = ROOT / "scheduler.py"
    line = f"{minute} {hour} * * * cd {ROOT} && {py} {script} run >> {ROOT}/cron.log 2>&1"
    print("# Add this line to your crontab (crontab -e):")
    print(line)


def main():
    parser = argparse.ArgumentParser(description="AI-powered daily commit automation")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run the daily job once immediately")
    sub.add_parser("daemon", help="Run as a background scheduler")
    sub.add_parser("status", help="Show current project and history")
    sub.add_parser("install", help="Print a crontab line for system cron")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run()
    elif args.command == "daemon":
        cmd_daemon()
    elif args.command == "status":
        cmd_status()
    elif args.command == "install":
        cmd_install()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
