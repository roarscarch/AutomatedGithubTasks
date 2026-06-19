"""Repo manager — create GitHub repos, clone them, scaffold initial structure."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)


def clone_repo(name, work_dir):
    """Clone an existing repo from the user's GitHub account into work_dir."""
    config = load_config()
    username = config.get("github_username", "roarscarch")
    target = Path(work_dir) / name
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gh", "repo", "clone", f"{username}/{name}", str(target)],
        check=True,
    )
    return target


def create_repo(idea):
    """Create a new public GitHub repo and clone it into the work directory."""
    config = load_config()
    name = idea["name"]
    desc = idea["description"]
    work_dir = ROOT / config.get("work_dir", "./repos")

    # Create the repo on GitHub (no clone yet)
    subprocess.run(
        ["gh", "repo", "create", name, "--description", desc, "--public"],
        check=True,
        capture_output=True,
    )

    # Clone into work_dir
    repo_path = clone_repo(name, work_dir)
    return repo_path


def bootstrap_scaffold(repo_path, idea):
    """Write the initial project files: README, license, basic structure."""
    readme = f"""# {idea['name'].replace('-', ' ').title()}

> {idea['tagline']}

{idea['description']}

## Stack
- Language: **{idea['language']}**
- {', '.join(idea['stack'])}

## Features
"""
    for f in idea["features"]:
        readme += f"- {f}\n"

    readme += f"""
## Architecture
{idea['architecture_note']}

## Getting Started
```bash
# Coming soon — this project is under active development.
```

*Built fresh every day by an AI-powered automation pipeline.*
"""

    (repo_path / "README.md").write_text(readme)

    # Language-specific scaffold
    lang = idea["language"]
    if lang == "python":
        (repo_path / "requirements.txt").write_text("# dependencies\n")
        (repo_path / "src").mkdir(exist_ok=True)
        (repo_path / "src" / "__init__.py").write_text("")
        (repo_path / "tests").mkdir(exist_ok=True)
        (repo_path / "tests" / "__init__.py").write_text("")
        (repo_path / ".gitignore").write_text(
            "__pycache__/\n*.pyc\n.venv/\n.env\n*.egg-info/\ndist/\n.pytest_cache/\n"
        )
    elif lang == "rust":
        subprocess.run(
            ["cargo", "init", "--name", idea["name"], str(repo_path)],
            check=True, capture_output=True,
        )
    elif lang == "go":
        (repo_path / "go.mod").write_text(
            f"module github.com/user/{idea['name']}\n\ngo 1.22\n"
        )
        (repo_path / "main.go").write_text("package main\n\nfunc main() {}\n")
    elif lang in ("typescript", "javascript"):
        (repo_path / "package.json").write_text(
            json.dumps({"name": idea["name"], "version": "0.1.0", "private": True}, indent=2)
        )
        (repo_path / "src").mkdir(exist_ok=True)
        (repo_path / "src" / "index.ts").write_text("// entry point\n")
        (repo_path / "tsconfig.json").write_text(
            json.dumps({
                "compilerOptions": {"target": "ES2022", "module": "ESNext", "strict": True},
                "include": ["src"],
            }, indent=2)
        )

    # Initial commit
    subprocess.run(["git", "-C", str(repo_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "chore: initial scaffold"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_path), "push", "origin", "main"], check=True)
