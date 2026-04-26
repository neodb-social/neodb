import base64
import json
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from common.sentry import count as sentry_count
from common.validators import get_safe_redirect_url

from ..models import WebAuthnCredential
from .account import auth_login

_VALID_TRANSPORTS = {"usb", "nfc", "ble", "hybrid", "internal"}
_CHALLENGE_TIMEOUT = 300  # 5 minutes


def _get_rp_id() -> str:
    return settings.WEBAUTHN_RP_ID


def _get_expected_origins() -> list[str]:
    """Return all valid origins for WebAuthn verification."""
    origins = [settings.WEBAUTHN_ORIGIN]
    for domain in getattr(settings, "ALTERNATIVE_DOMAINS", []):
        origins.append(f"https://{domain}")
    return origins


@require_http_methods(["POST"])
@login_required
def passkey_register_options(request):
    user = request.user
    existing_credentials = [
        PublicKeyCredentialDescriptor(id=cred.credential_id)
        for cred in user.webauthn_credentials.all()
    ]
    options = generate_registration_options(
        rp_id=_get_rp_id(),
        rp_name=settings.SITE_INFO.get("site_name", "NeoDB"),
        user_id=WebAuthnCredential.get_webauthn_user_id(user),
        user_name=user.username,
        user_display_name=user.display_name or user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=existing_credentials,
    )
    request.session["webauthn_register_challenge"] = {
        "challenge": base64.b64encode(options.challenge).decode("ascii"),
        "ts": time.time(),
    }
    return JsonResponse(json.loads(options_to_json(options)), safe=True)


@require_http_methods(["POST"])
@login_required
def passkey_register_verify(request):
    entry = request.session.pop("webauthn_register_challenge", None)
    if not entry:
        return HttpResponseBadRequest("No registration challenge in session")
    challenge_b64 = entry["challenge"]
    if time.time() - entry.get("ts", 0) > _CHALLENGE_TIMEOUT:
        return HttpResponseBadRequest("Registration challenge expired")

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponseBadRequest("Invalid JSON")

    try:
        challenge = base64.b64decode(challenge_b64)
        verification = verify_registration_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=_get_rp_id(),
            expected_origin=_get_expected_origins(),
        )
    except Exception as e:
        logger.warning(f"WebAuthn registration verification failed: {e}")
        return JsonResponse(
            {"ok": False, "error": _("Passkey registration failed")}, status=400
        )

    name = (body.get("name", "").strip() or _("Passkey"))[:255]
    raw_transports = body.get("transports", [])
    transports = [
        t for t in raw_transports if isinstance(t, str) and t in _VALID_TRANSPORTS
    ]

    try:
        WebAuthnCredential.objects.create(
            user=request.user,
            name=name,
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=transports,
        )
    except IntegrityError:
        return JsonResponse(
            {"ok": False, "error": _("Credential already registered")}, status=409
        )
    request.session["has_passkeys"] = True
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def passkey_login_options(request):
    options = generate_authentication_options(
        rp_id=_get_rp_id(),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    request.session["webauthn_login_challenge"] = {
        "challenge": base64.b64encode(options.challenge).decode("ascii"),
        "ts": time.time(),
    }
    return JsonResponse(json.loads(options_to_json(options)), safe=True)


@require_http_methods(["POST"])
def passkey_login_verify(request):
    sentry_count("login.attempt", attributes={"type": "passkey"})
    entry = request.session.pop("webauthn_login_challenge", None)
    if not entry:
        return HttpResponseBadRequest("No login challenge in session")
    challenge_b64 = entry["challenge"]
    if time.time() - entry.get("ts", 0) > _CHALLENGE_TIMEOUT:
        return HttpResponseBadRequest("Login challenge expired")

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponseBadRequest("Invalid JSON")

    raw_id_b64 = body.get("rawId") or body.get("id")
    if not raw_id_b64:
        return JsonResponse(
            {"ok": False, "error": _("Passkey not recognized")}, status=400
        )
    try:
        padding = "=" * (-len(raw_id_b64) % 4)
        credential_id_bytes = base64.urlsafe_b64decode(raw_id_b64 + padding)
    except Exception:
        return JsonResponse(
            {"ok": False, "error": _("Passkey not recognized")}, status=400
        )

    try:
        credential = WebAuthnCredential.objects.select_related("user").get(
            credential_id=credential_id_bytes
        )
    except WebAuthnCredential.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": _("Passkey not recognized")}, status=400
        )

    if not credential.user.is_active:
        return JsonResponse(
            {"ok": False, "error": _("Account is inactive")}, status=400
        )

    try:
        challenge = base64.b64decode(challenge_b64)
        verification = verify_authentication_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=_get_rp_id(),
            expected_origin=_get_expected_origins(),
            credential_public_key=credential.public_key,
            credential_current_sign_count=credential.sign_count,
        )
    except Exception as e:
        logger.warning(f"WebAuthn authentication verification failed: {e}")
        return JsonResponse(
            {"ok": False, "error": _("Passkey verification failed")}, status=400
        )

    WebAuthnCredential.objects.filter(
        pk=credential.pk, sign_count=credential.sign_count
    ).update(sign_count=verification.new_sign_count, last_used=timezone.now())

    auth_login(request, credential.user)

    next_url = get_safe_redirect_url(request.session.get("next_url"), "/")
    return JsonResponse({"ok": True, "redirect": next_url})


@require_http_methods(["POST"])
@login_required
def passkey_delete(request):
    try:
        body = json.loads(request.body)
        pk = int(body.get("id", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid request")

    deleted_count, _details = WebAuthnCredential.objects.filter(
        pk=pk, user=request.user
    ).delete()
    if not deleted_count:
        return JsonResponse({"ok": False, "error": _("Passkey not found")}, status=404)
    request.session["has_passkeys"] = request.user.webauthn_credentials.exists()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@login_required
def passkey_rename(request):
    try:
        body = json.loads(request.body)
        pk = int(body.get("id", 0))
        name = body.get("name", "").strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid request")

    if not name:
        return JsonResponse({"ok": False, "error": _("Name is required")}, status=400)

    updated = WebAuthnCredential.objects.filter(pk=pk, user=request.user).update(
        name=name[:255]
    )
    if not updated:
        return JsonResponse({"ok": False, "error": _("Passkey not found")}, status=404)
    return JsonResponse({"ok": True})
