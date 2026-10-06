import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint

MetricAttributes = Mapping[str, str | int | float | bool | None]

# The default EventScrubber matches keys exactly, so OAuth form fields need
# their own entries. takahe/takahe/settings.py keeps a copy of these helpers.
SENTRY_EXTRA_DENYLIST = ["access_token", "client_secret", "refresh_token"]
_SECRET_QUERY = re.compile(
    r"(?:^|[?&;])(?:api_key|access_token|client_secret|key|token)=", re.IGNORECASE
)


def _strip_secret_query(data: dict, url_key: str, query_key: str) -> None:
    url = data.get(url_key)
    if isinstance(url, str) and _SECRET_QUERY.search(url):
        data[url_key] = url.split("?", 1)[0]
    query = data.get(query_key)
    if isinstance(query, str) and _SECRET_QUERY.search(query):
        data[query_key] = ""


def before_send(event: "Event", hint: "Hint") -> "Event":
    """Drop cookies and credential-bearing query strings from an event or transaction."""
    request: Any = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in headers:
                if key.lower() == "cookie":
                    headers[key] = "[Filtered]"
        _strip_secret_query(request, "url", "query_string")
    spans: Any = event.get("spans") or []
    for span in spans:
        data = span.get("data") if isinstance(span, dict) else None
        if isinstance(data, dict):
            _strip_secret_query(data, "url", "http.query")
    return event


def before_breadcrumb(crumb: dict, hint: dict) -> dict:
    """The SDK keeps an outgoing request's query in `http.query`, which no
    scrubber key matches."""
    data = crumb.get("data")
    if isinstance(data, dict):
        _strip_secret_query(data, "url", "http.query")
    return crumb


def url_domain(url: str | None) -> str:
    if not url:
        return "unknown"
    parsed = urlparse(url if "://" in url else f"//{url}")
    return (parsed.hostname or "unknown").lower()


def _clean_attributes(attributes: MetricAttributes | None) -> dict[str, Any]:
    if not attributes:
        return {}
    return {key: value for key, value in attributes.items() if value is not None}


def count(
    key: str,
    value: int | float = 1,
    attributes: MetricAttributes | None = None,
) -> None:
    """Emit a Sentry counter metric when Sentry is configured."""
    try:
        import sentry_sdk
    except ImportError:
        return

    is_initialized = getattr(sentry_sdk, "is_initialized", None)
    if not callable(is_initialized) or not is_initialized():
        return

    metrics = getattr(sentry_sdk, "metrics", None)
    metrics_count = getattr(metrics, "count", None)
    if not callable(metrics_count):
        return

    try:
        metrics_count(key, value, attributes=_clean_attributes(attributes))
    except Exception:
        return


def record_activity(action: str, source: str) -> None:
    """Emit a `user.activity` counter for a user-initiated action.

    ``source`` is ``"api"`` or ``"web"``. Call this at the view/API layer;
    importer/exporter per-item processing should not call it (the import or
    export *start* is recorded by the triggering view instead).
    """
    count("user.activity", attributes={"action": action, "source": source})


def record_registration_captcha(outcome: str, reason: str = "") -> None:
    """Emit a `registration.captcha` counter for one captcha outcome.

    ``outcome`` is the coarse bucket (``issued``, ``passed``, ``wrong_answer``,
    ``bad_trace``, ``expired``, ``exhausted``, ``rate_limited``, ``fail_open``).
    For ``bad_trace``, ``reason`` names the check that rejected it, so a
    false-positive spike is diagnosable rather than merely visible.

    Attributes stay low-cardinality on purpose: never pass an IP address, item
    id, tile token or handle. Per-address state belongs in the cache, where the
    failure cap keeps it.
    """
    count(
        "registration.captcha",
        attributes={"outcome": outcome, "reason": reason or outcome},
    )


def record_catalog_edit(action: str, item_type: str, op: str = "") -> None:
    """Emit a `catalog.edit` counter for a user-initiated catalog change.

    ``action`` is the coarse bucket (``create``/``update``/``delete``, plus
    ``fetch``/``verify`` for the crawl and verification triggers); ``op`` names
    the specific view so a single bucket stays breakable down. ``item_type`` is
    ``Item.class_name``.

    Call this at the view layer only. Background refresh writes items through
    the same models, so a model-level hook could not tell the two apart.
    """
    count(
        "catalog.edit",
        attributes={"action": action, "type": item_type, "op": op or action},
    )
