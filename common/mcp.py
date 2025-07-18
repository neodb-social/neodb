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
    return [i.to_schema_org() for i in items]
    # {"data": items, "pages": num_pages, "count": count}
