"""Hermes-local extensions to browser-harness helpers.

This file IS edited by the agent. The vendored helpers.py at
/home/rj/opt/browser-harness/helpers.py is read-only — never edit it;
upstream updates would conflict.

When the agent needs a primitive that helpers.py doesn't provide
(e.g. upload_file, wait_for_selector, some site-specific shortcut), add
it HERE and keep using it from Hermes sessions going forward.
"""

from pathlib import Path as _Path
from urllib.parse import urlparse as _urlparse

from helpers import *  # noqa: F401,F403 — upstream primitives in scope
from helpers import goto as _vendor_goto

_HERMES_SKILLS = _Path.home() / ".hermes" / "browser_harness" / "skills"


def goto(url):
    """Wraps upstream goto() so Hermes-owned skills surface alongside vendor's.

    Upstream's goto returns {..., domain_skills: [...]} listing markdown under
    ~/opt/browser-harness/domain-skills/<host-segment>/. We additionally scan
    ~/.hermes/browser_harness/skills/<host-segment>/ and merge filenames in
    (de-duped, upstream wins on name collisions)."""
    result = _vendor_goto(url)
    host = (_urlparse(url).hostname or "").removeprefix("www.").split(".")[0]
    if not host:
        return result
    hermes_dir = _HERMES_SKILLS / host
    if not hermes_dir.is_dir():
        return result
    upstream = list(result.get("domain_skills", []))
    hermes_only = [p.name for p in sorted(hermes_dir.rglob("*.md")) if p.name not in upstream]
    return {**result, "domain_skills": (upstream + hermes_only)[:20]}
