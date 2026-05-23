from django.http import Http404
from django.shortcuts import get_object_or_404 as _get_object_or_404


def get_object_or_404(*args, **kwargs):
    """Wrapper that returns 404 for invalid PK types (e.g. non-numeric IDs)."""
    try:
        return _get_object_or_404(*args, **kwargs)
    except (ValueError, TypeError):
        raise Http404
