"""The render-on-push job.

The action definition lives in ``ci/render.yml`` (checkout, test, batch,
upload the ``shots/`` artifact). It does not post anywhere, and it is not
installed under ``.github/workflows``. This module is the check the tests
run against that file.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "ci" / "render.yml"

# Substrings that would mean the job sends the demo somewhere.
POST_MARKERS = (
    "curl ",
    "wget ",
    "twitter",
    "x.com",
    "webhook",
    "chat.postmessage",
    "ntfy",
    "discord.com",
    "hooks.slack.com",
)


def workflow_text(path: Path | None = None) -> str:
    return (path or WORKFLOW).read_text()


def workflow_violations(text: str) -> list[str]:
    """Empty when the action renders on push, uploads artifacts, and does not post."""
    problems = []
    lowered = text.lower()
    for marker in POST_MARKERS:
        if marker in lowered:
            problems.append(f"posts via {marker.strip()}")
    if "push" not in lowered:
        problems.append("does not run on push")
    if "upload-artifact" not in lowered:
        problems.append("does not upload artifacts")
    if "demo_director batch" not in text and "demo-director batch" not in text:
        problems.append("does not render the batch")
    if "unittest" not in lowered:
        problems.append("does not run the tests")
    if ".github/workflows" in lowered and "copy" not in lowered:
        problems.append("installed under .github/workflows")
    return problems
