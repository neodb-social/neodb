import os

import django_rq
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.config import *
from common.utils import GenerateDateUUIDMediaFilePath
from journal.exporters.doufen import export_marks_task
from journal.importers.douban import DoubanImporter
from journal.importers.goodreads import GoodreadsImporter
from journal.importers.letterboxd import LetterboxdImporter
from journal.importers.opml import OPMLImporter
from journal.models import reset_journal_visibility_for_user
from mastodon.api import *
from social.models import reset_social_visibility_for_user

from .account import *
from .tasks import *


@login_required
def preferences(request):
    preference = request.user.preference
    identity = request.user.identity
    if request.method == "POST":
        identity.anonymous_viewable = bool(request.POST.get("anonymous_viewable"))
        identity.save(update_fields=["anonymous_viewable"])
        preference.default_visibility = int(request.POST.get("default_visibility"))
        preference.mastodon_default_repost = (
            int(request.POST.get("mastodon_default_repost", 0)) == 1
        )
        preference.classic_homepage = int(request.POST.get("classic_homepage"))
        preference.hidden_categories = request.POST.getlist("hidden_categories")
        preference.post_public_mode = int(request.POST.get("post_public_mode"))
        preference.show_last_edit = bool(request.POST.get("show_last_edit"))
        preference.mastodon_repost_mode = int(
            request.POST.get("mastodon_repost_mode", 0)
        )
        preference.mastodon_append_tag = request.POST.get(
            "mastodon_append_tag", ""
        ).strip()
        preference.save(
            update_fields=[
                "default_visibility",
                "post_public_mode",
                "classic_homepage",
                "mastodon_append_tag",
                "mastodon_repost_mode",
                "mastodon_default_repost",
                "show_last_edit",
                "hidden_categories",
            ]
        )
        lang = request.POST.get("language")
        print(lang)
        if lang in dict(settings.LANGUAGES).keys() and lang != request.user.language:
            request.user.language = lang
            request.user.save(update_fields=["language"])
        clear_preference_cache(request)
    return render(
        request,
        "users/preferences.html",
        {"enable_local_only": settings.ENABLE_LOCAL_ONLY},
    )


@login_required
def data(request):
    return render(
        request,
        "users/data.html",
        {
            "allow_any_site": settings.MASTODON_ALLOW_ANY_SITE,
            "import_status": request.user.preference.import_status,
            "export_status": request.user.preference.export_status,
            "letterboxd_task": LetterboxdImporter.latest_task(request.user),
        },
    )


@login_required
def data_import_status(request):
    return render(
        request,
        "users/data_import_status.html",
        {
            "import_status": request.user.preference.import_status,
        },
    )


@login_required
def export_reviews(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    return render(request, "users/data.html")


@login_required
def export_marks(request):
    if request.method == "POST":
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
    if request.method == "POST" and request.user.mastodon_username:
        django_rq.get_queue("mastodon").enqueue(
            refresh_mastodon_data_task, request.user.pk
        )
        messages.add_message(request, messages.INFO, _("同步已开始。"))
    return redirect(reverse("users:info"))


@login_required
def sync_mastodon_preference(request):
    if request.method == "POST":
        request.user.preference.mastodon_skip_userinfo = (
            request.POST.get("mastodon_sync_userinfo", "") == ""
        )
        request.user.preference.mastodon_skip_relationship = (
            request.POST.get("mastodon_sync_relationship", "") == ""
        )
        request.user.preference.save()
        messages.add_message(request, messages.INFO, _("同步设置已保存。"))
    return redirect(reverse("users:info"))


@login_required
def reset_visibility(request):
    if request.method == "POST":
        visibility = int(request.POST.get("visibility"))
        visibility = visibility if visibility >= 0 and visibility <= 2 else 0
        reset_journal_visibility_for_user(request.user.identity, visibility)
        reset_social_visibility_for_user(request.user.identity, visibility)
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
            int(request.POST.get("visibility", 0)),
            int(request.POST.get("import_mode", 0)),
        )
        if importer.import_from_file(request.FILES["file"]):
            messages.add_message(request, messages.INFO, _("文件已上传，等待后台导入。"))
        else:
            messages.add_message(request, messages.ERROR, _("无法识别文件。"))
    return redirect(reverse("users:data"))


@login_required
def import_letterboxd(request):
    if request.method == "POST":
        f = (
            settings.MEDIA_ROOT
            + "/"
            + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
        )
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "wb+") as destination:
            for chunk in request.FILES["file"].chunks():
                destination.write(chunk)
        LetterboxdImporter.enqueue(
            request.user,
            visibility=int(request.POST.get("visibility", 0)),
            file=f,
        )
        messages.add_message(request, messages.INFO, _("文件已上传，等待后台导入。"))
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
            messages.add_message(request, messages.INFO, _("文件已上传，等待后台导入。"))
        else:
            messages.add_message(request, messages.ERROR, _("无法识别文件。"))
    return redirect(reverse("users:data"))
