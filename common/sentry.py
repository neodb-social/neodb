from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

MetricAttributes = Mapping[str, str | int | float | bool | None]


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
