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


def record_activity(action: str, source: str) -> None:
    """Emit a `user.activity` counter for a user-initiated action.

    ``source`` is ``"api"`` or ``"web"``. Call this at the view/API layer;
    importer/exporter per-item processing should not call it (the import or
    export *start* is recorded by the triggering view instead).
    """
    count("user.activity", attributes={"action": action, "source": source})


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
