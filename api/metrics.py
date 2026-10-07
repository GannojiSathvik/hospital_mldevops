"""Prometheus metric objects for the API.

Prometheus "scrapes" (periodically GETs) the ``/metrics`` endpoint and stores
these numbers as time series; Grafana draws dashboards from them. The names
below are part of the shared CONTRACT so dashboards and alerts can rely on them.

* Counter   - a number that only goes up (e.g. total predictions).
* Histogram - counts observations into buckets (e.g. latency), which lets
              Prometheus compute percentiles like p95 latency.

Labels (e.g. ``risk_level``) split one metric into several series. We keep
label values low-cardinality (fixed sets) so Prometheus stays efficient.
"""

from prometheus_client import Counter, Histogram

PREDICTIONS_TOTAL = Counter(
    "predictions_total",
    "Number of predictions served",
    ["endpoint", "risk_level"],
)

PREDICTION_LATENCY_SECONDS = Histogram(
    "prediction_latency_seconds",
    "Time spent computing predictions (model inference only)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests by method, route path and status code",
    ["method", "path", "status"],
)

API_ERRORS_TOTAL = Counter(
    "api_errors_total",
    "Unhandled server errors (HTTP 500)",
)

HIGH_RISK_PREDICTIONS_TOTAL = Counter(
    "high_risk_predictions_total",
    "Predictions whose risk level is High",
)

AUTH_FAILURES_TOTAL = Counter(
    "auth_failures_total",
    "Rejected requests: missing_key / invalid_key (401) or forbidden (403)",
    ["reason"],
)
