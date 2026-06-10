import re
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from loguru import logger

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
STEAM_ID_RE = re.compile(r"https://steamcommunity\.com/openid/id/(\d+)")


@login_required
def steam_openid_start(request):
    """Initiate Steam OpenID 2.0 login to verify the user's Steam ID."""
    return_to = request.build_absolute_uri(reverse("users:steam_openid_callback"))
    realm = settings.SITE_INFO["site_url"] + "/"

    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    redirect_url = f"{STEAM_OPENID_URL}?{urlencode(params)}"
    return redirect(redirect_url)


@login_required
def steam_openid_callback(request):
    """Handle the callback from Steam OpenID and extract the verified Steam ID."""
    params = dict(request.GET.items())

    if params.get("openid.mode") != "id_res":
        logger.warning(f"Steam OpenID: unexpected mode {params.get('openid.mode')}")
        return redirect(reverse("users:data"))

    # Verify the response with Steam
    verify_params = dict(params)
    verify_params["openid.mode"] = "check_authentication"
    try:
        resp = requests.post(STEAM_OPENID_URL, data=verify_params, timeout=10)
        if "is_valid:true" not in resp.text:
            logger.warning("Steam OpenID verification failed")
            return redirect(reverse("users:data"))
    except requests.RequestException:
        logger.exception("Steam OpenID verification request failed")
        return redirect(reverse("users:data"))

    # Extract Steam ID from claimed_id
    claimed_id = params.get("openid.claimed_id", "")
    match = STEAM_ID_RE.match(claimed_id)
    if not match:
        logger.warning(f"Steam OpenID: invalid claimed_id {claimed_id}")
        return redirect(reverse("users:data"))

    steam_id = match.group(1)
    request.session["steam_id"] = steam_id
    logger.info(f"Steam OpenID verified for steam_id={steam_id}")

    return redirect(reverse("users:steam_import_page"))


@login_required
def steam_import_page(request):
    """Show the Steam import settings page after successful OpenID login."""
    steam_id = request.session.get("steam_id", "")
    if not steam_id:
        return redirect(reverse("users:data"))

    return render(
        request,
        "users/steam_import.html",
        {"steam_id": steam_id},
    )
