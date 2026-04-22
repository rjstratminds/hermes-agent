"""Shared file safety rules used by both tools and ACP shims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _hermes_home_path() -> Path:
    """Resolve the active HERMES_HOME (profile-aware) without circular imports."""
    try:
        from hermes_constants import get_hermes_home  # local import to avoid cycles
        return get_hermes_home()
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written."""
    hermes_home = _hermes_home_path()
    paths = [
        os.path.join(home, ".ssh", "authorized_keys"),
        os.path.join(home, ".ssh", "id_rsa"),
        os.path.join(home, ".ssh", "id_ed25519"),
        os.path.join(home, ".ssh", "config"),
        str(hermes_home / ".env"),
        os.path.join(home, ".bashrc"),
        os.path.join(home, ".zshrc"),
        os.path.join(home, ".profile"),
        os.path.join(home, ".bash_profile"),
        os.path.join(home, ".zprofile"),
        os.path.join(home, ".netrc"),
        os.path.join(home, ".pgpass"),
        os.path.join(home, ".npmrc"),
        os.path.join(home, ".pypirc"),
        "/etc/sudoers",
        "/etc/passwd",
        "/etc/shadow",
    ]

    # browser-harness overlay self-heals by appending to helpers_hermes.py.
    # Prompt injection via page content could weaponize this to persist a
    # backdoor across sessions, so require explicit opt-in.
    if os.environ.get("HERMES_BROWSER_HARNESS_ALLOW_SELF_EDIT", "").lower() not in ("1", "true", "yes"):
        paths.append(str(hermes_home / "browser_harness" / "helpers_hermes.py"))

    return {os.path.realpath(p) for p in paths}


def build_write_denied_prefixes(home: str) -> list[str]:
    """Return sensitive directory prefixes that must never be written."""
    prefixes = [
        os.path.join(home, ".ssh"),
        os.path.join(home, ".aws"),
        os.path.join(home, ".gnupg"),
        os.path.join(home, ".kube"),
        "/etc/sudoers.d",
        "/etc/systemd",
        os.path.join(home, ".docker"),
        os.path.join(home, ".azure"),
        os.path.join(home, ".config", "gh"),
    ]

    # browser-harness overlay is agent-editable for self-healing, but that
    # same property makes it the highest-value target for prompt injection
    # carried in page content. Gate it behind an explicit opt-in env var so
    # the agent must get user consent before extending its own primitives.
    if os.environ.get("HERMES_BROWSER_HARNESS_ALLOW_SELF_EDIT", "").lower() not in ("1", "true", "yes"):
        hermes_home = _hermes_home_path()
        prefixes.append(str(hermes_home / "browser_harness" / "skills"))

    return [os.path.realpath(p) + os.sep for p in prefixes]


def get_safe_write_root() -> Optional[str]:
    """Return the resolved HERMES_WRITE_SAFE_ROOT path, or None if unset."""
    root = os.getenv("HERMES_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    """Return True if path is blocked by the write denylist or safe root."""
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    safe_root = get_safe_write_root()
    if safe_root and not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
        return True

    return False


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message when a read targets internal Hermes cache files."""
    resolved = Path(path).expanduser().resolve()
    hermes_home = _hermes_home_path().resolve()
    blocked_dirs = [
        hermes_home / "skills" / ".hub" / "index-cache",
        hermes_home / "skills" / ".hub",
    ]
    for blocked in blocked_dirs:
        try:
            resolved.relative_to(blocked)
        except ValueError:
            continue
        return (
            f"Access denied: {path} is an internal Hermes cache file "
            "and cannot be read directly to prevent prompt injection. "
            "Use the skills_list or skill_view tools instead."
        )
    return None
