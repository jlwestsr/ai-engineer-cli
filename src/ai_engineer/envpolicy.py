"""Shared environment allow/deny policy for .env loading and child processes."""

from __future__ import annotations

# Exact names refused by the .env loader and removed from scrubbed child envs.
CONTROL_DENY_EXACT = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONBREAKPOINT",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FORCE_FLAT_NAMESPACE",
        "NODE_OPTIONS",
        "RUBYOPT",
        "PERL5OPT",
        "JAVA_TOOL_OPTIONS",
    }
)

# Extra exact names refused only by the .env loader (child processes still need PATH).
ENVFILE_DENY_EXACT = CONTROL_DENY_EXACT | {"PATH"}

CONTROL_DENY_PREFIXES = ("GIT_", "PIP_", "UV_")

# Scrubbed from child processes only — parent still needs provider keys from .env.
SECRET_MARKERS = (
    "API_KEY",
    "API_TOKEN",
    "ACCESS_TOKEN",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "KEY_ID",
    "CREDENTIAL",
    "AUTHORIZATION",
)

SECRET_PREFIXES = ("SESSION_",)


def is_control_env_key(name: str, *, for_envfile: bool = False) -> bool:
    """Return True for process-control / loader keys that must not be injected."""
    upper = name.upper()
    deny_exact = ENVFILE_DENY_EXACT if for_envfile else CONTROL_DENY_EXACT
    if upper in deny_exact:
        return True
    return any(upper.startswith(prefix) for prefix in CONTROL_DENY_PREFIXES)


def is_secret_env_key(name: str) -> bool:
    """Return True for credential-shaped names that must not reach child processes."""
    upper = name.upper()
    if any(upper.startswith(prefix) for prefix in SECRET_PREFIXES):
        return True
    if any(marker in upper for marker in SECRET_MARKERS):
        return True
    return upper.endswith("_KEY") or "_KEY_" in upper


def is_denied_env_key(name: str, *, for_envfile: bool = False) -> bool:
    """Policy entry point.

    - ``for_envfile=True``: refuse control/loader keys only (API keys must still load).
    - ``for_envfile=False``: scrub control keys and credential-shaped names from children.
    """
    if is_control_env_key(name, for_envfile=for_envfile):
        return True
    if for_envfile:
        return False
    return is_secret_env_key(name)
