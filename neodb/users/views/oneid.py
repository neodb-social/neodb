from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from users.managed_community import bootstrap_managed_identity
from users.managed_identity import login_managed_identity
from users.oneid import (
    OneIDClient,
    OneIDConfigurationError,
    OneIDError,
    OneIDProviderError,
    OneIDValidationError,
)


@require_GET
def oneid_start(request):
    try:
        authorization_url = OneIDClient().authorization_url(request)
    except OneIDConfigurationError:
        return JsonResponse({"error": "oneid_not_configured"}, status=503)
    except OneIDError:
        return JsonResponse({"error": "oneid_unavailable"}, status=503)
    return redirect(authorization_url)


@require_GET
def oneid_callback(request):
    try:
        identity = OneIDClient().verify_callback(request)
    except OneIDConfigurationError:
        return JsonResponse({"error": "oneid_not_configured"}, status=503)
    except OneIDValidationError:
        return JsonResponse({"error": "oneid_verification_failed"}, status=400)
    except OneIDProviderError:
        return JsonResponse({"error": "oneid_unavailable"}, status=503)

    bootstrap_managed_identity(identity)
    login_managed_identity(request, identity)
    return redirect("/")
