from django.shortcuts import reverse, redirect, render, get_object_or_404
from django.http import HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import auth
from django.contrib.auth import authenticate
from django.core.paginator import Paginator
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count
from .models import User, Report, Preference
from .forms import ReportForm
from mastodon.api import *
from mastodon import mastodon_request_included
from common.config import *
from common.utils import PageLinksGenerator
from management.models import Announcement
from mastodon.models import MastodonApplication
from mastodon.api import verify_account
from django.conf import settings
from urllib.parse import quote
import django_rq
from .account import *
from .tasks import *
from datetime import timedelta
from django.utils import timezone
import json
from django.contrib import messages

from journal.importers.opml import OPMLImporter
from journal.importers.douban import DoubanImporter
from journal.importers.goodreads import GoodreadsImporter
from journal.exporters.doufen import export_marks_task
from journal.models import reset_visibility_for_user


@mastodon_request_included
@login_required
def preferences(request):
    preference = request.user.get_preference()
    if request.method == "POST":
        preference.default_visibility = int(request.POST.get("default_visibility"))
        preference.default_no_share = bool(request.POST.get("default_no_share"))
        preference.classic_homepage = int(request.POST.get("classic_homepage"))
        preference.mastodon_publish_public = bool(
            request.POST.get("mastodon_publish_public")
        )
        preference.show_last_edit = bool(request.POST.get("show_last_edit"))
        preference.mastodon_append_tag = request.POST.get(
            "mastodon_append_tag", ""
        ).strip()
        preference.save(
            update_fields=[
                "default_visibility",
                "default_no_share",
                "classic_homepage",
                "mastodon_publish_public",
                "mastodon_append_tag",
                "show_last_edit",
            ]
        )
    return render(request, "users/preferences.html")


@mastodon_request_included
@login_required
def data(request):
    return render(
        request,
        "users/data.html",
        {
            "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
            "import_status": request.user.get_preference().import_status,
            "export_status": request.user.get_preference().export_status,
        },
    )


@login_required
def data_import_status(request):
    return render(
        request,
        "users/data_import_status.html",
        {
            "import_status": request.user.get_preference().import_status,
        },
    )


@mastodon_request_included
@login_required
def export_reviews(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    return render(request, "users/data.html")


@mastodon_request_included
@login_required
def export_marks(request):
    if request.method == "POST":
        if not request.user.preference.export_status.get("marks_pending"):
            django_rq.get_queue("export").enqueue(export_marks_task, request.user)
            request.user.preference.export_status["marks_pending"] = True
            request.user.preference.save()
        messages.add_message(request, messages.INFO, _("导出已开始。"))
        return redirect(reverse("users:data"))
    else:
        try:
            with open(request.user.preference.export_status["marks_file"], "rb") as fh:
                response = HttpResponse(
                    fh.read(), content_type="application/vnd.ms-excel"
                )
                response["Content-Disposition"] = 'attachment;filename="marks.xlsx"'
                return response
        except Exception:
            messages.add_message(request, messages.ERROR, _("导出文件已过期，请重新导出"))
            return redirect(reverse("users:data"))


@login_required
def sync_mastodon(request):
    if request.method == "POST":
        django_rq.get_queue("mastodon").enqueue(
            refresh_mastodon_data_task, request.user
        )
        messages.add_message(request, messages.INFO, _("同步已开始。"))
    return redirect(reverse("users:data"))


@login_required
def reset_visibility(request):
    if request.method == "POST":
        visibility = int(request.POST.get("visibility"))
        visibility = visibility if visibility >= 0 and visibility <= 2 else 0
        reset_visibility_for_user(request.user, visibility)
        messages.add_message(request, messages.INFO, _("已重置。"))
    return redirect(reverse("users:data"))


@login_required
def import_goodreads(request):
    if request.method == "POST":
        raw_url = request.POST.get("url")
        if GoodreadsImporter.import_from_url(raw_url, request.user):
            messages.add_message(request, messages.INFO, _("链接已保存，等待后台导入。"))
        else:
            messages.add_message(request, messages.ERROR, _("无法识别链接。"))
    return redirect(reverse("users:data"))


@login_required
def import_douban(request):
    if request.method == "POST":
        importer = DoubanImporter(
            request.user,
            int(request.POST.get("visibility")),
            int(request.POST.get("import_mode")),
        )
        if importer.import_from_file(request.FILES["file"]):
            messages.add_message(request, messages.INFO, _("文件上传成功，等待后台导入。"))
        else:
            messages.add_message(request, messages.ERROR, _("无法识别文件。"))
    return redirect(reverse("users:data"))


@login_required
def import_opml(request):
    if request.method == "POST":
        importer = OPMLImporter(
            request.user,
            int(request.POST.get("visibility", 0)),
            int(request.POST.get("import_mode", 0)),
        )
        if importer.import_from_file(request.FILES["file"]):
            messages.add_message(request, messages.INFO, _("文件上传成功，等待后台导入。"))
        else:
            messages.add_message(request, messages.ERROR, _("无法识别文件。"))
    return redirect(reverse("users:data"))
