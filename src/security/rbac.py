"""Role-Based Access Control (RBAC) for the readmission API and ML pipelines (Lab 6).

What this module does
---------------------
1. Loads the policy file ``configs/access_control.yaml`` which maps each *role*
   (viewer, clinician, analyst, ml_engineer, admin) to a list of *permissions*
   such as ``predict:single`` or ``pipeline:train``.
2. Answers "may this role do this?" via :func:`has_permission`. The admin role
   holds the wildcard ``"*"`` which matches every permission.
3. Turns an API key presented by a caller into a :class:`Principal`
   (who are you + which role) via :func:`resolve_api_key`.
4. Protects command-line pipelines with :func:`require_pipeline_permission`.
5. Writes an append-only audit trail (``logs/audit.log``) with :func:`audit_log`.

Key ideas to explain in a viva
------------------------------
* **Authentication vs authorisation (401 vs 403).** Authentication answers
  "who are you?" — a missing or unknown API key fails here and the API returns
  **401 Unauthorized**. Authorisation answers "are you allowed to do this?" —
  a *valid* key whose role lacks the permission fails here and the API returns
  **403 Forbidden**. This module provides both halves: ``resolve_api_key``
  (authN) and ``has_permission`` (authZ).
* **Least privilege.** Each role gets only the permissions its job needs. A
  clinician can score one patient and see an explanation but cannot bulk-export
  predictions or retrain the model. Only admin has ``*``.
* **Why store only SHA-256 hashes of keys?** Keys arrive via the ``API_KEYS``
  environment variable (``name:role:key,name:role:key``). We immediately hash
  each key and keep only the hash in memory, so the raw secret is never stored
  in our data structures, never logged and never shown in a debugger dump of
  the key table.
* **Why ``hmac.compare_digest``?** A normal ``==`` comparison stops at the first
  differing byte, so an attacker timing many requests could learn a key byte
  by byte (a *timing attack*). ``compare_digest`` takes the same time no matter
  where the strings differ.
* **Audit log.** Every allow/deny decision is written as one JSON line with a
  UTC timestamp, the caller's *name* and role — never the raw key — so that
  security reviews can answer "who did what, when".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

DEFAULT_POLICY_PATH = "configs/access_control.yaml"
AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", "logs/audit.log"))
WILDCARD = "*"

# Module-level caches (cleared by reset_cache() in tests).
_policy_cache: dict[str, dict[str, Any]] = {}
_key_table_cache: dict[str, Any] = {}
_audit_lock = threading.Lock()


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: a human-friendly name and the role it holds."""

    name: str
    role: str


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------
def load_policy(path: str = DEFAULT_POLICY_PATH) -> dict:
    """Load and validate the RBAC policy YAML (cached per path).

    Returns the parsed dict; ``policy["roles"]`` maps role -> {"permissions": [...]}.
    Raises ``ValueError`` if the file is malformed or a role name is not one of
    the known roles listed under ``allowed_roles`` (rejecting unknown roles at
    load time means a typo like ``admn`` cannot silently create a new role).
    """
    key = str(path)
    if key in _policy_cache:
        return _policy_cache[key]

    with open(path, encoding="utf-8") as fh:
        policy = yaml.safe_load(fh) or {}

    roles = policy.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError(f"RBAC policy {path} has no 'roles' mapping")

    allowed_roles = set(policy.get("allowed_roles") or roles.keys())
    for role, spec in roles.items():
        if role not in allowed_roles:
            raise ValueError(f"Unknown role '{role}' in RBAC policy {path}")
        perms = (spec or {}).get("permissions")
        if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
            raise ValueError(f"Role '{role}' must have a list of string permissions")

    _policy_cache[key] = policy
    return policy


def has_permission(role: str, permission: str, policy: dict | None = None) -> bool:
    """Return True if ``role`` grants ``permission`` (``"*"`` grants everything).

    Unknown roles get no permissions (deny by default).
    """
    policy = policy or load_policy()
    spec = policy.get("roles", {}).get(role)
    if not spec:
        return False
    perms = spec.get("permissions", [])
    return WILDCARD in perms or permission in perms


def endpoint_permissions(policy: dict | None = None) -> dict[str, str]:
    """Return the documented endpoint -> permission map from the policy file."""
    policy = policy or load_policy()
    return dict(policy.get("endpoints", {}))


# ---------------------------------------------------------------------------
# API key handling (authentication)
# ---------------------------------------------------------------------------
def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key_table() -> list[tuple[str, Principal]]:
    """Parse ``API_KEYS`` into a list of (sha256(key), Principal).

    Re-parsed whenever the env var changes (cheap, and makes tests simple).
    Entries with an unknown role are rejected with ``ValueError`` so a
    misconfigured deployment fails loudly instead of granting odd access.
    """
    raw = os.getenv("API_KEYS", "")
    if _key_table_cache.get("raw_hash") == _sha256(raw):
        return _key_table_cache["table"]

    policy = load_policy()
    known_roles = set(policy["roles"].keys())
    table: list[tuple[str, Principal]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise ValueError("API_KEYS entries must look like name:role:key")
        name, role, key = (p.strip() for p in parts)
        if role not in known_roles:
            raise ValueError(f"API_KEYS entry '{name}' uses unknown role '{role}'")
        table.append((_sha256(key), Principal(name=name, role=role)))

    # Only the hash of the raw env string is cached, not the string itself.
    _key_table_cache["raw_hash"] = _sha256(raw)
    _key_table_cache["table"] = table
    return table


def resolve_api_key(api_key: str | None) -> Principal | None:
    """Map a presented API key to its Principal, or None if missing/unknown.

    The presented key is hashed and compared against every stored hash with
    ``hmac.compare_digest`` (constant time). We deliberately do not stop early
    on a match so total time does not depend on which entry matched.
    """
    if not api_key:
        return None
    presented = _sha256(api_key)
    found: Principal | None = None
    for stored_hash, principal in _key_table():
        if hmac.compare_digest(stored_hash, presented):
            found = principal
    return found


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
def require_pipeline_permission(permission: str) -> Principal:
    """Guard for CLI pipelines: check ``PIPELINE_API_KEY`` has ``permission``.

    Raises ``PermissionError`` if the key is missing, unknown, or the role lacks
    the permission. Every decision is written to the audit log.
    """
    principal = resolve_api_key(os.getenv("PIPELINE_API_KEY"))
    if principal is None:
        audit_log("unknown", "none", permission, "pipeline", False, reason="invalid_or_missing_key")
        raise PermissionError("PIPELINE_API_KEY is missing or invalid")
    allowed = has_permission(principal.role, permission)
    audit_log(principal.name, principal.role, permission, "pipeline", allowed)
    if not allowed:
        raise PermissionError(f"Role '{principal.role}' is not permitted to perform '{permission}'")
    return principal


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
def audit_log(
    principal_name: str,
    role: str,
    action: str,
    resource: str,
    allowed: bool,
    **extra: Any,
) -> None:
    """Append one JSON line describing an access decision to ``logs/audit.log``.

    Never pass raw API keys here — only the principal *name* is recorded.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "principal": principal_name,
        "role": role,
        "action": action,
        "resource": resource,
        "allowed": bool(allowed),
        **extra,
    }
    path = Path(os.getenv("AUDIT_LOG_PATH", str(AUDIT_LOG_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str)
    with _audit_lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def reset_cache() -> None:
    """Clear cached policy and key table (used by tests after changing env)."""
    _policy_cache.clear()
    _key_table_cache.clear()
