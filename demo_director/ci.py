"""The GitHub Action document lives at ci/render.yml.

It renders on push and uploads artifacts. It does not post anywhere.
This seat does not install it under .github/workflows.
"""
from __future__ import annotations

import pathlib
import re

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / "ci" / "render.yml"

# Destinations a render job must not call. Matched case-insensitively.
FORBIDDEN = (
    "api.x.com",
    "api.twitter.com",
    "upload.twitter.com",
    "hooks.slack.com",
    "discord.com/api/webhooks",
)


def workflow_text() -> str:
    return WORKFLOW.read_text()


def posts_anywhere(text: str) -> list[str]:
    lowered = text.lower()
    found = [token for token in FORBIDDEN if token in lowered]
    if re.search(r"\bcurl\b[\s\S]{0,160}\bpost\b", lowered):
        found.append("curl POST")
    return found
