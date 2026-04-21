"""Helpers for loading Hermes .env files consistently across entrypoints."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# Env var name suffixes that indicate credential values.  These are the
# only env vars whose values we sanitize on load — we must not silently
# alter arbitrary user env vars, but credentials are known to require
# pure ASCII (they become HTTP header values).
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")
_ONECLI_M3_ENV = Path.home() / ".config" / "onecli" / "hermes-m3-proxy.env"
_ONECLI_M3_HOST = "openclaw-gcp.tailc13f7e.ts.net"
_ONECLI_M3_PORT = 10255
_ONECLI_PROXY_KEYS = (
    "ONECLI_PROXY_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _sanitize_loaded_credentials() -> None:
    """Strip non-ASCII characters from credential env vars in os.environ.

    Called after dotenv loads so the rest of the codebase never sees
    non-ASCII API keys.  Only touches env vars whose names end with
    known credential suffixes (``_API_KEY``, ``_TOKEN``, etc.).
    """
    for key, value in list(os.environ.items()):
        if not any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
            continue
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            os.environ[key] = value.encode("ascii", errors="ignore").decode("ascii")


def _load_dotenv_with_fallback(path: Path, *, override: bool) -> None:
    try:
        load_dotenv(dotenv_path=path, override=override, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(dotenv_path=path, override=override, encoding="latin-1")
    # Strip non-ASCII characters from credential env vars that were just
    # loaded.  API keys must be pure ASCII since they're sent as HTTP
    # header values (httpx encodes headers as ASCII).  Non-ASCII chars
    # typically come from copy-pasting keys from PDFs or rich-text editors
    # that substitute Unicode lookalike glyphs (e.g. ʋ U+028B for v).
    _sanitize_loaded_credentials()


def default_onecli_proxy_env_path() -> Path:
    """Return the default Hermes M3 OneCli proxy env file path."""
    return _ONECLI_M3_ENV


def _onecli_proxy_env_disabled(value: str) -> bool:
    return value.strip().lower() in {"0", "false", "off", "none", "no"}


def _validate_onecli_m3_proxy_env(path: Path) -> None:
    """Validate the local Hermes M3 OneCli handoff before loading it.

    The rj-m3max handoff is intentionally scoped to the OneCli injection
    proxy on openclaw-gcp port 10255.  Refuse stale local-gateway files or
    accidental references to the OneCli API port.
    """
    from urllib.parse import urlsplit

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")

    gateway_url = values.get("ONECLI_GATEWAY_URL", "")
    parsed_gateway = urlsplit(gateway_url)
    if parsed_gateway.hostname != _ONECLI_M3_HOST or parsed_gateway.port != _ONECLI_M3_PORT:
        raise RuntimeError(
            f"{path} must point ONECLI_GATEWAY_URL at "
            f"{_ONECLI_M3_HOST}:{_ONECLI_M3_PORT}, not {gateway_url!r}"
        )

    for key in _ONECLI_PROXY_KEYS:
        value = values.get(key, "")
        if not value:
            continue
        parsed = urlsplit(value)
        if parsed.hostname != _ONECLI_M3_HOST or parsed.port != _ONECLI_M3_PORT:
            raise RuntimeError(
                f"{path} must point {key} at "
                f"{_ONECLI_M3_HOST}:{_ONECLI_M3_PORT}, not {value!r}"
            )


def load_onecli_proxy_env(
    env_file: str | os.PathLike | None = None,
    *,
    override: bool = True,
    validate_m3_handoff: bool = True,
) -> Path | None:
    """Load the Hermes M3 OneCli proxy env file when present.

    ``HERMES_ONECLI_PROXY_ENV`` may point at an alternate file or disable
    loading with ``0``, ``false``, ``off``, ``none``, or ``no``.
    """
    configured = os.getenv("HERMES_ONECLI_PROXY_ENV")
    if configured is not None:
        if _onecli_proxy_env_disabled(configured):
            return None
        env_path = Path(configured).expanduser()
    elif env_file is not None:
        env_path = Path(env_file).expanduser()
    else:
        return None

    if not env_path.exists():
        return None

    if validate_m3_handoff:
        _validate_onecli_m3_proxy_env(env_path)
    _load_dotenv_with_fallback(env_path, override=override)
    return env_path


def _sanitize_env_file_if_needed(path: Path) -> None:
    """Pre-sanitize a .env file before python-dotenv reads it.

    python-dotenv does not handle corrupted lines where multiple
    KEY=VALUE pairs are concatenated on a single line (missing newline).
    This produces mangled values — e.g. a bot token duplicated 8×
    (see #8908).

    We delegate to ``hermes_cli.config._sanitize_env_lines`` which
    already knows all valid Hermes env-var names and can split
    concatenated lines correctly.
    """
    if not path.exists():
        return
    try:
        from hermes_cli.config import _sanitize_env_lines
    except ImportError:
        return  # early bootstrap — config module not available yet

    read_kw = {"encoding": "utf-8", "errors": "replace"}
    try:
        with open(path, **read_kw) as f:
            original = f.readlines()
        sanitized = _sanitize_env_lines(original)
        if sanitized != original:
            import tempfile
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".env_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(sanitized)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception:
        pass  # best-effort — don't block gateway startup


def load_hermes_dotenv(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
    onecli_proxy_env: str | os.PathLike | None = None,
) -> list[Path]:
    """Load Hermes environment files with user config taking precedence.

    Behavior:
    - `~/.hermes/.env` overrides stale shell-exported values when present.
    - project `.env` acts as a dev fallback and only fills missing values when
      the user env exists.
    - if no user env exists, the project `.env` also overrides stale shell vars.
    """
    loaded: list[Path] = []

    home_path = Path(hermes_home or os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    user_env = home_path / ".env"
    project_env_path = Path(project_env) if project_env else None

    # Fix corrupted .env files before python-dotenv parses them (#8908).
    if user_env.exists():
        _sanitize_env_file_if_needed(user_env)

    if user_env.exists():
        _load_dotenv_with_fallback(user_env, override=True)
        loaded.append(user_env)

    if project_env_path and project_env_path.exists():
        _load_dotenv_with_fallback(project_env_path, override=not loaded)
        loaded.append(project_env_path)

    onecli_env = load_onecli_proxy_env(onecli_proxy_env, override=True)
    if onecli_env is not None:
        loaded.append(onecli_env)

    return loaded
