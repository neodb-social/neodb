import functools

from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from requests.exceptions import Timeout


def mastodon_request_included(func):
    """Handles timeout exception of requests to mastodon, returns http 500"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (Timeout, ConnectionError):
            return render(
                args[0], "common/error.html", {"msg": _("联邦宇宙请求超时叻_(´ཀ`」 ∠)__ ")}
            )

    return wrapper


class HttpResponseInternalServerError(HttpResponse):
    status_code = 500
