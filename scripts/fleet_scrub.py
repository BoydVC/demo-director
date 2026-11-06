#!/usr/bin/env python3
"""Fleet scrub: list name-blocked repos, or propose and patch synthetic names.

See README. This does not read private tutor sheets or rebuild an hspt bank.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo_director.scrub import main

if __name__ == "__main__":
    sys.exit(main())
