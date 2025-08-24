from django.conf import settings


def site_info(request):
    context = settings.SITE_INFO
    context["debug_enabled"] = settings.DEBUG
    return context
