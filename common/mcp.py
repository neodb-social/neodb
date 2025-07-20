"""
MCP (Model Context Protocol) integration for NeoDB

This module provides MCP tools and resources for external AI clients to
interact with NeoDB data and functionality.
"""

from asgiref.sync import sync_to_async
from django.conf import settings
from django_mcp import mcp_app as mcp
from mcp.server.fastmcp.server import Context


@mcp.tool()
def get_server_info() -> dict:
    """
    Get basic information about this NeoDB server instance.

    Returns server metadata including name, version, and description.
    """
    return {
        "name": settings.SITE_INFO.get("site_name", "NeoDB"),
        "description": settings.SITE_INFO.get("site_description", ""),
        "version": settings.NEODB_VERSION,
    }


@mcp.tool()
async def get_current_user_info(context: Context) -> dict:
    """
    Get information about the currently authenticated user.

    Returns:
        Current user's profile information.
    """
    request = context.request_context.request
    if not request or not request.scope:
        return {"error": "Request context not available"}
    user = request.scope.get("user")
    if not user:
        return {"error": "User not authenticated"}

    return await _get_current_user_info(user)


@sync_to_async
def _get_current_user_info(user) -> dict:
    return {
        "username": user.username,
        "display_name": user.display_name,
        "url": settings.SITE_INFO["site_url"] + user.url,
        "avatar": user.avatar,
        "joined": user.date_joined.isoformat(),
    }


@mcp.tool()
async def search_catalog(
    context: Context, query: str, category: str | None = None
) -> list | dict:
    """
    Search the NeoDB catalog for book, movie, tv, music, game, podcast and performance.

    Args:
        query (str): The search query string.
        category (str | None): Optional category filter, can be one of: book, movie, tv, music, game, podcast, performance.

    Returns:
        List of items matching the query.
    """
    request = context.request_context.request
    if not request or not request.scope:
        return {"error": "Request context not available"}
    user = request.scope.get("user")
    if not user:
        return {"error": "User not authenticated"}
    query = query.strip()
    if not query:
        return {"error": "Invalid query"}
    return await _search_catalog(request, query, category)


@sync_to_async
def _search_catalog(request, query: str, category: str | None = None) -> list:
    from catalog.search.models import query_index
    # from journal.models.mark import Mark
    # from journal.models.rating import Rating

    categories = category.split(",") if category else None
    exclude_categories = (
        request.user.preference.hidden_categories
        if not categories and request.user.is_authenticated
        else None
    )
    page = 1
    items, num_pages, count, _, _ = query_index(
        query,
        page=page,
        categories=categories,
        prepare_external=False,
        exclude_categories=exclude_categories,
    )
    # Rating.attach_to_items(items)
    # if request.user.is_authenticated:
    #     Mark.attach_to_items(request.user.identity, items, request.user)
    return [i.ap_object for i in items]
    # {"data": items, "pages": num_pages, "count": count}


@mcp.tool()
async def add_note(
    context: Context,
    item_url: str,
    content: str,
    title: str | None = None,
    progress_type: str | None = None,
    progress_value: str | None = None,
    visibility: int = 0,
) -> dict:
    """
    Add a quote or note to a catalog item.

    Args:
        item_url (str): The full URL path of the item to add a note to.
        content (str): The content of the note.
        title (str | None): Optional title for the note.
        progress_type (str | None): Optional progress type (page, chapter, part, episode, track, cycle, timestamp, percentage).
        progress_value (str | None): Optional progress value (e.g. "50").
        visibility (int): Visibility level (0=Public, 1=Followers, 2=Private). Defaults to 0.

    Returns:
        Dictionary containing the created note information or error details.
    """
    request = context.request_context.request
    if not request or not request.scope:
        return {"error": "Request context not available"}
    user = request.scope.get("user")
    if not user:
        return {"error": "User not authenticated"}
    if not content.strip():
        return {"error": "Note content cannot be empty"}
    return await _add_note(
        user, item_url, content, title, progress_type, progress_value, visibility
    )


@sync_to_async
def _add_note(
    user,
    item_url: str,
    content: str,
    title: str | None = None,
    progress_type: str | None = None,
    progress_value: str | None = None,
    visibility: int = 0,
) -> dict:
    from catalog.common.models import Item
    from journal.models.note import Note

    try:
        item = Item.get_by_url(item_url)
        if not item:
            return {"error": f"Item with URL {item_url} not found"}
    except Exception as e:
        return {"error": f"Invalid item URL {item_url}: {str(e)}"}
    if visibility not in [0, 1, 2]:
        return {"error": "Visibility must be 0 (Public), 1 (Followers), or 2 (Private)"}
    valid_progress_types = [
        "page",
        "chapter",
        "part",
        "episode",
        "track",
        "cycle",
        "timestamp",
        "percentage",
    ]
    if progress_type and progress_type not in valid_progress_types:
        return {
            "error": f"Invalid progress_type. Must be one of: {', '.join(valid_progress_types)}"
        }
    if progress_type and not progress_value:
        return {"error": "progress_value is required when progress_type is specified"}
    try:
        note = Note.objects.create(
            owner=user.identity,
            item=item,
            content=content.strip(),
            title=title.strip() if title else None,
            progress_type=progress_type,
            progress_value=progress_value,
            visibility=visibility,
        )
        return {
            "success": True,
            "note": {
                "title": note.title,
                "content": note.content,
                "progress_type": note.progress_type,
                "progress_value": note.progress_value,
                "visibility": note.visibility,
                "created_time": note.created_time.isoformat(),
                "item": item.ap_object,
            },
        }
    except Exception as e:
        return {"error": f"Failed to create note: {str(e)}"}


@mcp.tool()
async def mark_item(
    context: Context,
    item_url: str,
    shelf_type: str,
    comment: str | None = None,
    rating: int | None = None,
    tags: list[str] | None = None,
    visibility: int | None = None,
) -> dict:
    """
    Mark an item on a shelf (wishlist, progress, complete, dropped).

    Args:
        item_url (str): The full URL path of the item to mark.
        shelf_type (str): The shelf to add the item to (wishlist, progress, complete, dropped).
        comment (str | None): Optional comment about the item.
        rating (int | None): Optional rating from 1-10.
        tags (list[str] | None): Optional list of tags.
        visibility (int | None): Visibility level (0=Public, 1=Followers, 2=Private). Uses user default if not specified.
        post_to_fediverse (bool): Whether to cross-post to social media. Defaults to False.

    Returns:
        Dictionary containing the mark information or error details.
    """
    request = context.request_context.request
    if not request or not request.scope:
        return {"error": "Request context not available"}
    user = request.scope.get("user")
    if not user:
        return {"error": "User not authenticated"}
    return await _mark_item(
        user, item_url, shelf_type, comment, rating, tags, visibility
    )


@sync_to_async
def _mark_item(
    user,
    item_url: str,
    shelf_type: str,
    comment: str | None = None,
    rating: int | None = None,
    tags: list[str] | None = None,
    visibility: int | None = None,
) -> dict:
    from catalog.common.models import Item
    from journal.models.mark import Mark
    from journal.models.shelf import ShelfType

    try:
        item = Item.get_by_url(item_url)
        if not item:
            return {"error": f"Item with URL {item_url} not found"}
    except Exception as e:
        return {"error": f"Invalid item URL {item_url}: {str(e)}"}

    if shelf_type not in ShelfType.values:
        return {
            "error": f"Invalid shelf_type. Must be one of: {', '.join(ShelfType.values)}"
        }
    if rating is not None and (rating < 1 or rating > 10):
        return {"error": "Rating must be between 1 and 10"}
    if visibility is not None and visibility not in [0, 1, 2]:
        return {"error": "Visibility must be 0 (Public), 1 (Followers), or 2 (Private)"}
    if visibility is None:
        visibility = user.preference.default_visibility
    try:
        mark = Mark(user.identity, item)
        mark.update(
            shelf_type=getattr(ShelfType, shelf_type.upper()),
            comment_text=comment or "",
            rating_grade=rating or 0,
            tags=tags or [],
            visibility=visibility,
        )
        return {
            "success": True,
            "mark": {
                "shelf_type": mark.shelf_type,
                "comment": mark.comment_text,
                "rating": mark.rating_grade or None,
                "tags": mark.tags,
                "visibility": mark.visibility,
                "created_time": mark.created_time.isoformat()
                if mark.created_time
                else None,
                "item": item.ap_object,
            },
        }
    except Exception as e:
        return {"error": f"Failed to mark item: {str(e)}"}
