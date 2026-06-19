"""Calls DeepSeek to generate a novel, buildable project idea each week."""
import json
import os
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).resolve().parent


def load_config():
    with open(ROOT / "config.json") as f:
        return json.load(f)


def get_api_key():
    return os.environ.get("DEEPSEEK_API_KEY") or load_config().get("deepseek_api_key")


IDEA_PROMPT = """You are a creative software architect. Invent ONE original, interesting,
and buildable software project. It should be small enough that one person could build
a meaningful prototype in one week with 5-10 commits per day.

The project must be genuinely useful or fun — not another todo app or weather dashboard.
Pick something with technical depth: a novel algorithm, an unusual integration,
a clever data structure, a visualization technique, a CLI tool that solves a real pain point,
a tiny language, a protocol implementation, a creative simulation, etc.

Return ONLY valid JSON — no markdown, no commentary:

{
  "name": "kebab-case-project-name",
  "description": "One sentence that makes someone want to clone it.",
  "tagline": "4-6 word pitch",
  "language": "python|rust|go|typescript|zig",
  "stack": ["framework-or-library", "another-if-needed"],
  "features": ["feature 1", "feature 2", "feature 3", "feature 4", "feature 5"],
  "architecture_note": "Short note on the internal design or interesting technical choice."
}"""


def generate_idea():
    """Call DeepSeek and return a parsed project idea dict."""
    config = load_config()
    client = OpenAI(
        api_key=get_api_key(),
        base_url=config["deepseek_base_url"],
    )

    resp = client.chat.completions.create(
        model=config.get("deepseek_model", "deepseek-chat"),
        messages=[{"role": "user", "content": IDEA_PROMPT}],
        temperature=0.9,
        max_tokens=800,
    )

    raw = resp.choices[0].message.content.strip()
    # Strip markdown fences if the model wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]

    idea = json.loads(raw)
    return idea
