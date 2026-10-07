"""FastAPI security dependency: API-key authentication + RBAC authorisation.

Usage in a route::

    @app.post("/predict")
    def predict(..., principal: Principal = Depends(require_permission("predict:single"))):

How a request is checked:

1. Read the ``X-API-Key`` header.
2. **Authentication** - no header, or a key that does not match any configured
   key -> **401 Unauthorized** ("we don't know who you are"). The response
   carries ``WWW-Authenticate: ApiKey`` as HTTP requires for 401.
3. **Authorisation** - key is valid but the role lacks the permission ->
   **403 Forbidden** ("we know who you are, but you may not do this").
   Retrying with the same key will never help, which is why it is a different
   code from 401.
4. Every decision (allowed or denied) is written to ``logs/audit.log`` with
   the principal's *name* - never the raw key - and failures increment the
   ``auth_failures_total{reason}`` Prometheus counter so attacks are visible.

Error messages are deliberately short and never echo the presented key back.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from api.metrics import AUTH_FAILURES_TOTAL
from src.security.rbac import Principal, audit_log, has_permission, resolve_api_key

# auto_error=False so *we* decide the status code (FastAPI would default to 403).
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _resource(request: Request) -> str:
    return f"{request.method} {request.url.path}"


def require_permission(permission: str | None) -> Callable[..., Principal]:
    """Build a dependency that enforces ``permission`` for the route.

    ``permission=None`` means "any valid key" (authentication only) - used by
    ``/whoami`` so every role can discover what it is allowed to do.
    """

    def dependency(
        request: Request,
        api_key: str | None = Security(api_key_header),
    ) -> Principal:
        request_id = getattr(request.state, "request_id", None)
        client_ip = request.client.host if request.client else None

        if not api_key:
            AUTH_FAILURES_TOTAL.labels(reason="missing_key").inc()
            audit_log(
                "anonymous",
                "none",
                permission or "authenticated",
                _resource(request),
                False,
                reason="missing_key",
                request_id=request_id,
                client_ip=client_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        principal = resolve_api_key(api_key)
        if principal is None:
            AUTH_FAILURES_TOTAL.labels(reason="invalid_key").inc()
            audit_log(
                "unknown",
                "none",
                permission or "authenticated",
                _resource(request),
                False,
                reason="invalid_key",
                request_id=request_id,
                client_ip=client_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if permission is not None and not has_permission(principal.role, permission):
            AUTH_FAILURES_TOTAL.labels(reason="forbidden").inc()
            audit_log(
                principal.name,
                principal.role,
                permission or "authenticated",
                _resource(request),
                False,
                reason="forbidden",
                request_id=request_id,
                client_ip=client_ip,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' lacks permission '{permission}'",
            )

        audit_log(
            principal.name,
            principal.role,
            permission or "authenticated",
            _resource(request),
            True,
            request_id=request_id,
            client_ip=client_ip,
        )
        request.state.principal = principal
        return principal

    return dependency
