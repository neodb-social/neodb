import functools
import uuid
from typing import TYPE_CHECKING

import django_rq
from discord import SyncWebhook
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.core.signing import b62_decode
from django.db.models.fields.files import FieldFile
from django.http import Http404, HttpRequest, HttpResponseRedirect, QueryDict
from django.utils import timezone
from django.utils.translation import gettext as _
from storages.backends.s3boto3 import S3Boto3Storage

from .config import ITEMS_PER_PAGE, ITEMS_PER_PAGE_OPTIONS, PAGE_LINK_NUMBER
from .models import int_

if TYPE_CHECKING:
    from users.models import APIdentity, User


class S3Storage(S3Boto3Storage):
    """
    Custom override backend that makes webp files store correctly
    """

    def get_object_parameters(self, name: str):
        params = super().get_object_parameters(name)
        if name.endswith(".webp"):
            params["ContentDisposition"] = "inline"
            params["ContentType"] = "image/webp"
        return params


class AuthedHttpRequest(HttpRequest):
    """
    A subclass of HttpRequest for type-checking only
    """

    user: "User"
    identity: "APIdentity | None"
    target_identity: "APIdentity"


class HTTPResponseHXRedirect(HttpResponseRedirect):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self["HX-Redirect"] = self["Location"]

    status_code = 200


def get_file_absolute_url(cover: FieldFile) -> str | None:
    if not cover or cover == settings.DEFAULT_ITEM_COVER:
        return None
    url = cover.url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{settings.SITE_INFO['site_url']}{url}"


def user_identity_required(func):
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and not request.identity:
            return HttpResponseRedirect("/account/register")
        return func(request, *args, **kwargs)

    return wrapper


def target_identity_required(func):
    @functools.wraps(func)
    def wrapper(request, user_name, *args, **kwargs):
        from users.models import APIdentity

        try:
            target = APIdentity.get_by_handle(user_name)
            restricted = target.restricted
        except APIdentity.DoesNotExist:
            raise Http404(_("User not found"))
        target_user = target.user
        viewer = None
        if target_user and not target_user.is_active:
            raise Http404(_("User no longer exists"))
        if request.user.is_authenticated:
            viewer = request.identity
            if not viewer:
                return HttpResponseRedirect("/account/register")
            if request.user != target_user:
                if restricted and not viewer.is_following(target):
                    raise PermissionDenied(_("Access denied"))
                if target.is_blocking(viewer) or target.is_blocked_by(viewer):
                    raise PermissionDenied(_("Access denied"))
        elif restricted:
            # anonymous can't view restricted identity
            raise PermissionDenied(_("Access denied"))
        elif not target.local:
            # anonymous can't view non local identity
            raise PermissionDenied(_("Login required"))
        elif not target.anonymous_viewable:
            raise PermissionDenied(_("Login required"))
        else:
            viewer = None
        request.target_identity = target
        request.identity = viewer
        return func(request, user_name, *args, **kwargs)

    return wrapper


def profile_identity_required(func):
    @functools.wraps(func)
    def wrapper(request, user_name, *args, **kwargs):
        from users.models import APIdentity

        try:
            target = APIdentity.get_by_handle(user_name)
            # this should trigger ObjectDoesNotExist if Takahe identity is not sync-ed
            restricted = target.restricted
        except ObjectDoesNotExist:
            raise Http404(_("User not found"))
        target_user = target.user
        viewer = None
        if target_user and not target_user.is_active:
            raise Http404(_("User no longer exists"))
        if request.user.is_authenticated:
            viewer = request.identity
            if not viewer:
                return HttpResponseRedirect("/account/register")
            if request.user != target_user:
                if target.is_blocking(viewer) or target.is_blocked_by(viewer):
                    raise PermissionDenied(_("Access denied"))
        else:
            viewer = None
        if restricted and (not viewer or not viewer.is_following(target)):
            raise PermissionDenied(_("Access denied"))
        request.target_identity = target
        request.identity = viewer
        return func(request, user_name, *args, **kwargs)

    return wrapper


def get_page_size_from_request(request) -> int:
    per_page = ITEMS_PER_PAGE
    if request:
        try:
            if request.GET.get("per_page"):
                per_page = int_(request.GET.get("per_page"))
            elif request.COOKIES.get("per_page"):
                per_page = int_(request.COOKIES.get("per_page"))
        except ValueError:
            pass
        if per_page not in ITEMS_PER_PAGE_OPTIONS:
            per_page = ITEMS_PER_PAGE
    return per_page


class CustomPaginator(Paginator):
    def __init__(self, object_list, request=None) -> None:
        per_page = get_page_size_from_request(request)
        super().__init__(object_list, per_page)


class PageLinksGenerator:
    # TODO inherit django paginator
    """
    Calculate the pages for multiple links pagination.
    length -- the number of page links in pagination
    """

    def __init__(
        self, current_page: int, total_pages: int, query: QueryDict | None = None
    ):
        length = PAGE_LINK_NUMBER
        current_page = int_(current_page)
        self.query_string = ""
        if query:
            q = query.copy()
            if q.get("page"):
                q.pop("page")
            self.query_string = q.urlencode()
        if self.query_string:
            self.query_string += "&"
        self.current_page = current_page
        self.previous_page = current_page - 1 if current_page > 1 else None
        self.next_page = current_page + 1 if current_page < total_pages else None
        self.start_page = 1
        self.end_page = 1
        self.page_range = None
        self.has_prev = None
        self.has_next = None

        start_page = current_page - length // 2
        end_page = current_page + length // 2

        # decision is based on the start page and the end page
        # both sides overflow
        if (start_page < 1 and end_page > total_pages) or length >= total_pages:
            self.start_page = 1
            self.end_page = total_pages
            self.has_prev = False
            self.has_next = False

        elif start_page < 1 and not end_page > total_pages:
            self.start_page = 1
            # this won't overflow because the total pages are more than the length
            self.end_page = end_page - (start_page - 1)
            self.has_prev = False
            if end_page == total_pages:
                self.has_next = False
            else:
                self.has_next = True

        elif not start_page < 1 and end_page > total_pages:
            self.end_page = total_pages
            self.start_page = start_page - (end_page - total_pages)
            self.has_next = False
            if start_page == 1:
                self.has_prev = False
            else:
                self.has_prev = True

        # both sides do not overflow
        elif not start_page < 1 and not end_page > total_pages:
            self.start_page = start_page
            self.end_page = end_page
            self.has_prev = True
            self.has_next = True

        self.first_page = 1
        self.last_page = total_pages
        self.page_range = range(self.start_page, self.end_page + 1)
        # assert self.has_prev is not None and self.has_next is not None


def GenerateDateUUIDMediaFilePath(filename, path_root):
    ext = filename.split(".")[-1]
    filename = "%s.%s" % (uuid.uuid4(), ext)
    root = ""
    if path_root.endswith("/"):
        root = path_root
    else:
        root = path_root + "/"
    return root + timezone.now().strftime("%Y/%m/%d") + f"{filename}"


def get_uuid_or_404(uuid_b62):
    try:
        i = b62_decode(uuid_b62)
        return uuid.UUID(int=i)
    except ValueError:
        raise Http404("Malformed Base62 UUID")


def discord_send(channel, content, **args) -> bool:
    dw = settings.DISCORD_WEBHOOKS.get(channel) or settings.DISCORD_WEBHOOKS.get(
        "default"
    )
    if not dw:
        return False
    if "thread_name" in args:
        args["thread_name"] = args["thread_name"][:99]
    django_rq.get_queue("fetch").enqueue(_discord_send, dw, content[:1989], **args)
    return True


def _discord_send(dw, content, **args):
    webhook = SyncWebhook.from_url(dw)
    webhook.send(content, **args)
