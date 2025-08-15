from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from boofilsic import __version__
from catalog.views import search as catalog_search
from journal.views import search as journal_search
from social.views import search as timeline_search
from takahe.models import Domain
from takahe.utils import Takahe
from users.models.user import User

from .api import api


def render_error(request: HttpRequest, title, message=""):
    return render(
        request, "common/error.html", {"msg": title, "secondary_msg": message}
    )


def opensearch(request):
    return render(request, "common/opensearch.xml.tpl", content_type="text/xml")


def manifest(request):
    return render(request, "common/manifest.json.tpl", content_type="application/json")


def share(request):
    q = request.GET.get("url") or request.GET.get("text") or request.GET.get("title")
    return redirect(reverse("common:search") + "?q=" + q) if q else home(request)


@login_required
def me(request):
    return redirect(request.user.identity.url)


def search(request):
    match request.GET.get("c", default="all").strip().lower():
        case "journal":
            return journal_search(request)
        case "timeline":
            return timeline_search(request)
        case _:
            return catalog_search(request)


def home(request):
    if request.user.is_authenticated:
        home = request.user.preference.classic_homepage
        if home == 1:
            return redirect(request.user.url)
        elif home == 2:
            return redirect(reverse("social:feed"))
        else:
            return redirect(reverse("catalog:discover"))
    else:
        return redirect(reverse("catalog:discover"))


def ap_redirect(request, uri):
    return redirect(request.get_full_path().replace("/~neodb~/", "/"))


def nodeinfo2(request, version: str):
    if version not in ["2.0", "2.1", "2.2"]:
        return _error_response(
            request, 400, default_message="NodeInfo version not supported"
        )
    usage = cache.get("nodeinfo_usage", default={})
    return JsonResponse(
        {
            "version": version,
            "software": {
                "name": "neodb",
                "version": __version__,
                "repository": "https://github.com/neodb-social/neodb",
                "homepage": "https://neodb.net/",
            },
            "instance": {
                "name": settings.SITE_INFO["site_name"],
                "description": settings.SITE_INFO["site_description"],
            },
            "protocols": ["activitypub", "neodb"],
            "openRegistrations": not settings.INVITE_ONLY,
            "services": {"outbound": [], "inbound": []},
            "usage": usage,
            "metadata": {
                "nodeName": settings.SITE_INFO["site_name"],
                "nodeRevision": settings.NEODB_VERSION,
                "nodeEnvironment": "development" if settings.DEBUG else "production",
            },
        }
    )


def _error_response(request, status: int, exception=None, default_message=""):
    message = str(exception) if exception else default_message
    if request.headers.get("HTTP_ACCEPT", "").endswith("json"):
        return JsonResponse({"error": message}, status=status)
    if (
        request.headers.get("HTTP_HX_REQUEST") is not None
        and request.headers.get("HTTP_HX_BOOSTED") is None
    ):
        return HttpResponse(message, status=status)
    return render(
        request,
        f"{status}.html",
        status=status,
        context={"message": message, "exception": exception},
    )


def error_400(request, exception=None):
    if isinstance(exception, DisallowedHost):
        url = settings.SITE_INFO["site_url"] + request.get_full_path()
        return redirect(url, permanent=True)
    return _error_response(request, 400, exception, "invalid request")


def error_403(request, exception=None):
    return _error_response(request, 403, exception, "forbidden")


def error_404(request, exception=None):
    request.session.pop("next_url", None)
    return _error_response(request, 404, exception, "not found")


def error_500(request, exception=None):
    return _error_response(request, 500, exception, "something wrong")


def console(request):
    token = None
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect(reverse("users:login"))
        app = Takahe.get_or_create_app(
            "Dev Console",
            settings.SITE_INFO["site_url"],
            "",
            owner_pk=0,
            client_id="app-00000000000-dev",
        )
        token = Takahe.refresh_token(app, request.user.identity.pk, request.user.pk)
    context = {
        "version": settings.NEODB_VERSION,
        "api": api,
        "token": token,
        "openapi_json_url": reverse(f"{api.urls_namespace}:openapi-json"),
    }
    return render(request, "console.html", context)


def oauth_protected_resource(request):
    """Return OAuth 2.0 Resource Server Metadata."""
    base_url = "" if settings.DEBUG else settings.SITE_INFO["site_url"].rstrip("/")
    if not base_url:
        scheme = "https" if request.is_secure() else "http"
        base_url = f"{scheme}://{request.get_host()}"
    metadata = {
        "resource": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "revocation_endpoint": f"{base_url}/oauth/revoke",
        "token_types_supported": ["bearer"],
        "scopes_supported": ["read", "write"],
        "resource_server": {
            "name": settings.SITE_INFO.get("site_name", "NeoDB"),
            "description": settings.SITE_INFO.get("site_description", ""),
            "version": settings.NEODB_VERSION,
        },
        "api_version": "1.0.0",
        "api_base_url": f"{base_url}/api",
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "response_types_supported": ["code"],
        "service_documentation": f"{base_url}/developer/",
        # "contact": settings.SITE_INFO.get("admin_email", ""),
    }
    return JsonResponse(metadata)


def oauth_authorization_server(request):
    """Return OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    base_url = "" if settings.DEBUG else settings.SITE_INFO["site_url"].rstrip("/")
    if not base_url:
        scheme = "https" if request.is_secure() else "http"
        base_url = f"{scheme}://{request.get_host()}"

    metadata = {
        # Required fields
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        # Supported grant types
        "grant_types_supported": ["authorization_code", "client_credentials"],
        # Supported response types
        "response_types_supported": ["code"],
        # Supported scopes
        "scopes_supported": ["read", "write"],
        # Token endpoint authentication methods
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
        # Additional endpoints
        "revocation_endpoint": f"{base_url}/oauth/revoke",
        # Server capabilities
        "code_challenge_methods_supported": ["plain", "S256"],
        # Response modes
        "response_modes_supported": ["query", "fragment"],
        # Service documentation
        "service_documentation": f"{base_url}/developer/",
        # Server information
        "op_policy_uri": f"{base_url}/about/",
        # Additional metadata
        "ui_locales_supported": ["en"],
        # Claim types supported
        "claim_types_supported": ["normal"],
        # Claims supported (basic user info)
        "claims_supported": ["sub", "username", "display_name", "avatar", "url"],
        # Authorization server features
        "request_parameter_supported": False,
        "request_uri_parameter_supported": False,
        "require_request_uri_registration": False,
        "registration_endpoint": f"{base_url}/api/v1/apps",
        "introspection_endpoint": f"{base_url}/api/token",
        "userinfo_endpoint": f"{base_url}/api/me",
        # JWKS - not used since we don't use JWT tokens
        "jwks_uri": None,
        # Additional server info
        "software_id": "neodb",
        "software_version": settings.NEODB_VERSION,
    }

    return JsonResponse(metadata)


def about(request):
    context = {
        "neodb_version": settings.NEODB_VERSION,
    }
    context["catalog_stats"] = cache.get("catalog_stats") or []
    context["instance_info_stats"] = cache.get("instance_info_stats") or {}
    context["invite_only"] = settings.INVITE_ONLY
    context["admin_users"] = User.objects.filter(is_superuser=True, is_active=True)
    context["staff_users"] = User.objects.filter(
        is_staff=True, is_superuser=False, is_active=True
    )
    peers = []
    for peer in Takahe.get_neodb_peers():
        d = Domain.objects.filter(domain=peer).first()
        if d:
            name = (d.nodeinfo or {}).get("metadata", {}).get("nodeName", peer)
            peers.append({"name": name, "url": f"https://{peer}"})
    context["neodb_peers"] = peers
    return render(request, "common/about.html", context)


def signup(request, code: str | None = None):
    if request.user.is_authenticated:
        return redirect(reverse("common:home"))
    if code:
        return redirect(reverse("users:login") + "?invite=" + code)
    return redirect(reverse("users:login"))
