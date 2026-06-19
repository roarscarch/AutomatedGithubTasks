"""AI-driven commit engine — DeepSeek writes code, we commit it."""
import json
import os
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).resolve().parent


def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)


def get_api_key():
    return os.environ.get("DEEPSEEK_API_KEY") or load_config().get("deepseek_api_key")


# ---------------------------------------------------------------------------
# Robust JSON repair for DeepSeek output
# ---------------------------------------------------------------------------

def parse_commit_json(raw):
    """Parse DeepSeek JSON output with progressive repair attempts."""
    # Attempt 1: straight parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: find the outermost { ... } pair
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 3: fix unterminated strings by finding the last valid key
    # Find all key-value pairs that are complete
    try:
        repaired = _repair_truncated(raw)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Attempt 4: regex extraction of individual fields
    return _extract_fields(raw)


def _repair_truncated(raw):
    """Close unterminated strings and add missing closing braces."""
    result = []
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
        if ch == "\n" and in_string:
            result.append("\\n")  # real newline in string → escape it
            continue
        result.append(ch)

    repaired = "".join(result)

    # Close unterminated string at EOF
    if in_string:
        repaired += '"'

    # Add missing closing braces/brackets
    open_braces = repaired.count("{") - repaired.count("}")
    open_brackets = repaired.count("[") - repaired.count("]")
    repaired += "}" * open_braces
    repaired += "]" * open_brackets

    return repaired


def _extract_fields(raw):
    """Regex fallback — pull out type, message, file, content individually."""
    import re

    def extract(key):
        # Match "key": "value" where value may contain escaped quotes
        pattern = rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"'
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            return m.group(1)
        return None

    commit_type = extract("type") or "chore"
    message = extract("message") or "update"
    filepath = extract("file") or "update.py"
    content = extract("content")

    # If content wasn't matched cleanly, grab everything after "content":
    if not content:
        m = re.search(r'"content"\s*:\s*"(.*)', raw, re.DOTALL)
        if m:
            content = m.group(1)
            # Strip trailing junk
            if content.rfind('"}') != -1:
                content = content[:content.rfind('"}')]
            content = content.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

    if not content:
        content = "# TODO\n"

    return {
        "type": commit_type,
        "message": message,
        "file": filepath,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Repo snapshot helpers
# ---------------------------------------------------------------------------

def repo_file_list(repo_path):
    """Return a flat list of tracked files in the repo."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files"],
        capture_output=True, text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def repo_recent_log(repo_path, n=8):
    """Return the last N commit messages for context."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--oneline", f"-{n}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def read_file_snippet(repo_path, filepath, max_lines=40):
    """Read a small snippet from a repo file for context."""
    full = repo_path / filepath
    if not full.exists():
        return ""
    lines = full.read_text().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines)} total lines)"


def snapshot(repo_path, idea):
    """Build a context string describing the current state of the repo."""
    files = repo_file_list(repo_path)
    log = repo_recent_log(repo_path)
    ctx = f"""Project: {idea['name']} — {idea['tagline']}
Description: {idea['description']}
Language: {idea['language']}
Stack: {', '.join(idea['stack'])}
Features planned: {', '.join(idea['features'])}

Current files in repo ({len(files)}):
{chr(10).join(f'  - {f}' for f in files[:40])}

Recent commits:
{log}

Key files (snippets):
"""
    # Include snippets of a few important files
    important = [f for f in files if f.endswith(('.py', '.rs', '.go', '.ts', '.js', '.md'))
                 and 'lock' not in f and 'node_modules' not in f]
    for f in important[:5]:
        snippet = read_file_snippet(repo_path, f)
        if snippet:
            ctx += f"\n--- {f} ---\n{snippet}\n"
    return ctx


# ---------------------------------------------------------------------------
# AI commit generation
# ---------------------------------------------------------------------------

COMMIT_PROMPT = """You are an expert software engineer contributing to an open-source project.
Below is the current state of the repo. Generate the NEXT logical commit — something
that moves the project forward in a real, useful way.

Think like a developer who is building this project incrementally:
- Early on: scaffold, core types, basic CLI, config loading, data models
- Midway: implement features, wire up integrations, add logic
- Later: tests, error handling, edge cases, documentation, polish

Return ONLY a single raw JSON object (no markdown fences, no commentary).

The "content" field contains source code. Escape it properly for JSON:
- Double-quotes inside content MUST be backslash-escaped: \"
- Backslashes MUST be doubled: \\\\
- Real newlines MUST be written as \\n
- Never use triple-quotes or raw strings — standard JSON escaping only.

{
  "type": "feat|fix|refactor|docs|test|chore",
  "message": "concise conventional-commit message, all lowercase, imperative mood",
  "file": "relative/path/to/new-or-existing-file",
  "content": "fully escaped source code here"
}

The file path should make sense for the language and project structure.
The code must be complete and correct — no placeholders, no TODOs.
Output ONLY the JSON object, properly escaped."""


def generate_commit(client, model, repo_path, idea):
    """Ask DeepSeek for the next commit given the current repo state."""
    ctx = snapshot(repo_path, idea)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COMMIT_PROMPT},
            {"role": "user", "content": ctx},
        ],
        temperature=0.8,
        max_tokens=2000,
    )

    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]

    return parse_commit_json(raw)


# ---------------------------------------------------------------------------
# Commit application
# ---------------------------------------------------------------------------

def apply_commit(repo_path, commit_data):
    """Write the file and create a git commit."""
    file_path = repo_path / commit_data["file"]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(commit_data["content"])

    subprocess.run(
        ["git", "-C", str(repo_path), "add", commit_data["file"]],
        check=True,
    )
    msg = commit_data["message"]
    # Strip type prefix if DeepSeek already included it
    for prefix in ("feat:", "fix:", "refactor:", "docs:", "test:", "chore:"):
        if msg.startswith(prefix):
            msg = msg[len(prefix):].strip()
            break
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m",
         f"{commit_data['type']}: {msg}"],
        check=True,
    )


def push_changes(repo_path):
    """Push main to origin."""
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "origin", "main"],
        check=True,
    )


# ---------------------------------------------------------------------------
# Main entry point for a daily run
# ---------------------------------------------------------------------------

def run(repo_path, idea):
    """Make 5-10 AI-generated commits to the given repo."""
    config = load_config()
    min_c = config.get("commits_min", 5)
    max_c = config.get("commits_max", 10)
    max_retries = config.get("max_retries", 3)
    retry_delay = config.get("retry_delay_seconds", 30)

    num_commits = random.randint(min_c, max_c)

    client = OpenAI(
        api_key=get_api_key(),
        base_url=config["deepseek_base_url"],
    )
    model = config.get("deepseek_model", "deepseek-chat")

    print(f"[{datetime.now().isoformat()}] Project: {idea['name']} — {num_commits} commits today")
    succeeded = 0

    for i in range(num_commits):
        for attempt in range(max_retries):
            try:
                commit_data = generate_commit(client, model, repo_path, idea)
                apply_commit(repo_path, commit_data)
                print(f"  [{i + 1}/{num_commits}] {commit_data['type']}: {commit_data['message']}")
                succeeded += 1
                break
            except Exception as e:
                print(f"  [{i + 1}] attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    print(f"  [{i + 1}] FAILED after {max_retries} attempts")

    push_changes(repo_path)
    print(f"[{datetime.now().isoformat()}] Done — {succeeded}/{num_commits} commits pushed.\n")
    return succeeded
