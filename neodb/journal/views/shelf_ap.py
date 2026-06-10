"""ActivityPub endpoints for NeoDB Shelf objects.

Shelves are not addressable by uuid, so they get their own per-handle
URLs:

- ``/users/<handle>/shelf/<shelf_type>``           — Shelf envelope
- ``/users/<handle>/shelf/<shelf_type>/items``     — paginated items

Both endpoints return ``application/activity+json`` regardless of the
``Accept`` header — there is no HTML rendering at these URLs (the
human-facing per-category view lives at
``/users/<handle>/profile/<category>/<shelf_type>/items``).

Visibility/auth is identical to the Collection AP view: signatures are
verified when present, ``is_visible_to_identity`` gates the response,
404 (not 403) is returned on denial.
"""

from __future__ import annotations

from django.http import Http404

from journal.models import Shelf
from users.models import APIdentity

from .collection import _list_ap_object_view, _list_items_view


def _resolve_shelf(handle: str, shelf_type: str) -> Shelf:
    # ``handle`` is a username (local) or full ``user@domain`` (remote);
    # APIdentity.get_by_handle handles both shapes. It raises
    # ``DoesNotExist`` on miss; map that to 404.
    try:
        identity = APIdentity.get_by_handle(handle)
    except APIdentity.DoesNotExist:
        raise Http404
    shelf = Shelf.objects.filter(owner=identity, shelf_type=shelf_type).first()
    if shelf is None:
        raise Http404
    return shelf


def shelf_ap_retrieve(request, handle: str, shelf_type: str):
    shelf = _resolve_shelf(handle, shelf_type)
    return _list_ap_object_view(request, shelf)


def shelf_ap_items(request, handle: str, shelf_type: str):
    shelf = _resolve_shelf(handle, shelf_type)
    return _list_items_view(request, shelf)
