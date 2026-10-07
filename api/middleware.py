"""HTTP middleware: request IDs, secure headers, metrics and safe error handling.

A *middleware* wraps every request/response. This one does four jobs:

1. **Request ID** - reuse the client's ``X-Request-ID`` (if it looks safe) or
   generate a UUID, expose it to logging, and echo it in the response header.
2. **Secure headers** - tell browsers to behave safely:
   * ``X-Content-Type-Options: nosniff`` - don't guess content types (stops
     a CSV being executed as script).
   * ``X-Frame-Options: DENY`` - page may not be framed (clickjacking).
   * ``Referrer-Policy: no-referrer`` - don't leak URLs to other sites.
   * ``Content-Security-Policy`` - only load resources from ourselves. The
     Swagger UI at /docs needs its CDN, so a relaxed policy is used only there.
   * ``Strict-Transport-Security`` - browsers must use HTTPS from now on.
   * ``Cache-Control: no-store`` - patient predictions must not be cached by
     browsers or proxies.
3. **Metrics** - count requests by method / route template / status code.
   We use the route *template* (``/predict``), not the raw URL, so random URLs
   cannot explode the number of Prometheus series.
4. **Last-resort error handler** - any unexpected exception becomes a generic
   ``{"detail": "Internal server error", "request_id": ...}`` 500 response. The
   stack trace goes to the server log only, never to the client (it could
   reveal file paths, library versions or configuration).
"""

from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.logging_config import get_logger, request_id_ctx
from api.metrics import API_ERRORS_TOTAL, HTTP_REQUESTS_TOTAL

logger = get_logger("api.middleware")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}
# Swagger UI loads JS/CSS from jsDelivr and an inline init script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# The browser UI (ui/) is served from this same origin and uses no inline
# scripts/styles and no third-party CDNs, so it can run under a strict policy:
# it may only load files from, and send requests to, this API.
UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)
UI_PATH = "/ui"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request ID + secure headers + request metrics + generic 500s."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - convert anything to a safe 500
                API_ERRORS_TOTAL.inc()
                logger.exception("Unhandled error on %s %s", request.method, request.url.path)
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Internal server error", "request_id": request_id},
                )

            route = request.scope.get("route")
            path_label = getattr(route, "path", "unmatched")
            HTTP_REQUESTS_TOTAL.labels(request.method, path_label, str(response.status_code)).inc()

            for name, value in SECURE_HEADERS.items():
                response.headers.setdefault(name, value)
            if request.url.path.startswith(DOCS_PATHS):
                response.headers["Content-Security-Policy"] = DOCS_CSP
            elif request.url.path.startswith(UI_PATH):
                response.headers["Content-Security-Policy"] = UI_CSP
            response.headers["X-Request-ID"] = request_id

            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)
