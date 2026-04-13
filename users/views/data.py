import copy
import csv
import datetime
import os

import django_rq
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.db.models import Min
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models import SiteConfig
from common.utils import GenerateDateUUIDMediaFilePath
from journal.exporters import CsvExporter, DoufenExporter, NdjsonExporter
from journal.importers import (
    CsvImporter,
    DoubanImporter,
    GoodreadsImporter,
    LetterboxdImporter,
    NdjsonImporter,
    OPMLImporter,
    SteamImporter,
    StoryGraphImporter,
    TraktImporter,
)
from journal.models import ShelfType
from journal.models.common import VisibilityType
from takahe.models import InboxMessage
from takahe.utils import Takahe
from users.models import Task

from .account import clear_preference_cache


def preferences(request):
    if not request.user.is_authenticated:
        return render(request, "users/preferences_anonymous.html")
    preference = request.user.preference
    identity = request.user.identity
    if request.method == "POST":
        identity.anonymous_viewable = bool(request.POST.get("anonymous_viewable"))
        identity.save(update_fields=["anonymous_viewable"])
        tidentity = Takahe.get_identity(identity.pk)
        tidentity.indexable = bool(request.POST.get("anonymous_viewable"))
        tidentity.save(update_fields=["indexable"])

        preference.default_visibility = int(request.POST.get("default_visibility", 0))
        preference.mastodon_default_repost = (
            int(request.POST.get("mastodon_default_repost", 0)) == 1
        )
        preference.mastodon_boost_enabled = (
            int(request.POST.get("mastodon_boost_enabled", 0)) == 1
        )
        preference.classic_homepage = int(request.POST.get("classic_homepage", 0))
        preference.hidden_categories = request.POST.getlist("hidden_categories")
        preference.auto_bookmark_cats = request.POST.getlist("auto_bookmark_cats")
        preference.post_public_mode = int(request.POST.get("post_public_mode", 0))
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
                "auto_bookmark_cats",
                "mastodon_repost_mode",
                "mastodon_default_repost",
                "mastodon_boost_enabled",
                "show_last_edit",
                "hidden_categories",
            ]
        )
        lang = request.POST.get("language")
        if lang in dict(settings.LANGUAGES).keys() and lang != request.user.language:
            request.user.language = lang
            translation.activate(lang)
            request.LANGUAGE_CODE = translation.get_language()
            request.user.save(update_fields=["language"])
        clear_preference_cache(request)
    return render(
        request,
        "users/preferences.html",
        {"enable_local_only": SiteConfig.system.enable_local_only},
    )


@login_required
def data(request):
    current_year = datetime.date.today().year
    queryset = request.user.identity.shelf_manager.get_shelf(
        ShelfType.COMPLETE
    ).members.all()
    start_date = queryset.aggregate(Min("created_time"))["created_time__min"]
    start_year = start_date.year if start_date else current_year
    years = reversed(range(start_year, current_year + 1))

    # Import tasks - check for both CSV and NDJSON importers
    csv_import_task = CsvImporter.latest_task(request.user)
    ndjson_import_task = NdjsonImporter.latest_task(request.user)
    # Use the most recent import task for display
    if ndjson_import_task and (
        not csv_import_task
        or ndjson_import_task.created_time > csv_import_task.created_time
    ):
        neodb_import_task = ndjson_import_task
    else:
        neodb_import_task = csv_import_task

    return render(
        request,
        "users/data.html",
        {
            "allow_any_site": len(SiteConfig.system.mastodon_login_whitelist) == 0,
            "import_task": DoubanImporter.latest_task(request.user),
            "export_task": DoufenExporter.latest_task(request.user),
            "csv_export_task": CsvExporter.latest_task(request.user),
            "neodb_import_task": neodb_import_task,  # Use the most recent import task
            "ndjson_export_task": NdjsonExporter.latest_task(request.user),
            "letterboxd_task": LetterboxdImporter.latest_task(request.user),
            "goodreads_task": GoodreadsImporter.latest_task(request.user),
            "storygraph_task": StoryGraphImporter.latest_task(request.user),
            "steam_task": SteamImporter.latest_task(request.user),
            "trakt_task": TraktImporter.latest_task(request.user),
            # "opml_task": OPMLImporter.latest_task(request.user),
            "years": years,
        },
    )


@login_required
def user_task_status(request, task_type: str):
    match task_type:
        case "journal.csvimporter":
            task_cls = CsvImporter
        case "journal.ndjsonimporter":
            task_cls = NdjsonImporter
        case "journal.csvexporter":
            task_cls = CsvExporter
        case "journal.ndjsonexporter":
            task_cls = NdjsonExporter
        case "journal.letterboxdimporter":
            task_cls = LetterboxdImporter
        case "journal.goodreadsimporter":
            task_cls = GoodreadsImporter
        case "journal.storygraphimporter":
            task_cls = StoryGraphImporter
        case "journal.opmlimporter":
            task_cls = OPMLImporter
        case "journal.doubanimporter":
            task_cls = DoubanImporter
        case "journal.steamimporter":
            task_cls = SteamImporter
        case "journal.traktimporter":
            task_cls = TraktImporter
        case _:
            return redirect(reverse("users:data"))
    task = task_cls.latest_task(request.user)
    return render(request, "users/user_task_status.html", {"task": task})


@login_required
def user_task_download(request, task_type: str):
    match task_type:
        case "journal.csvexporter":
            task_cls = CsvExporter
        case "journal.ndjsonexporter":
            task_cls = NdjsonExporter
        case _:
            return redirect(reverse("users:data"))
    task = task_cls.latest_task(request.user)
    if not task or task.state != Task.States.complete or not task.metadata.get("file"):
        messages.add_message(request, messages.ERROR, _("Export file not available."))
        return redirect(reverse("users:data"))
    response = HttpResponse()
    response["X-Accel-Redirect"] = (
        settings.MEDIA_URL + task.metadata["file"][len(settings.MEDIA_ROOT) :]
    )
    response["Content-Type"] = "application/zip"
    response["Content-Disposition"] = f'attachment; filename="{task.filename}.zip"'
    return response


@login_required
def export_reviews(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    return render(request, "users/data.html")


@login_required
def export_marks(request):
    # TODO: deprecated
    if request.method == "POST":
        DoufenExporter.create(request.user).enqueue()
        messages.add_message(request, messages.INFO, _("Generating exports."))
        return redirect(reverse("users:data"))
    else:
        task = DoufenExporter.latest_task(request.user)
        if not task or task.state != Task.States.complete:
            messages.add_message(
                request, messages.ERROR, _("Export file not available.")
            )
            return redirect(reverse("users:data"))
        try:
            with open(task.metadata["file"], "rb") as fh:
                response = HttpResponse(
                    fh.read(), content_type="application/vnd.ms-excel"
                )
                response["Content-Disposition"] = 'attachment;filename="marks.xlsx"'
                return response
        except Exception:
            messages.add_message(
                request, messages.ERROR, _("Export file expired. Please export again.")
            )
            return redirect(reverse("users:data"))


@login_required
def export_csv(request):
    if request.method == "POST":
        task = CsvExporter.latest_task(request.user)
        if (
            task
            and task.state not in [Task.States.complete, Task.States.failed]
            and task.created_time > (timezone.now() - datetime.timedelta(hours=1))
        ):
            messages.add_message(
                request, messages.INFO, _("Recent export still in progress.")
            )
            return redirect(reverse("users:data"))
        CsvExporter.create(request.user).enqueue()
        return redirect(
            reverse("users:user_task_status", args=("journal.csvexporter",))
        )
    return redirect(reverse("users:data"))


@login_required
def export_ndjson(request):
    if request.method == "POST":
        task = NdjsonExporter.latest_task(request.user)
        if (
            task
            and task.state not in [Task.States.complete, Task.States.failed]
            and task.created_time > (timezone.now() - datetime.timedelta(hours=1))
        ):
            messages.add_message(
                request, messages.INFO, _("Recent export still in progress.")
            )
            return redirect(reverse("users:data"))
        NdjsonExporter.create(request.user).enqueue()
        return redirect(
            reverse("users:user_task_status", args=("journal.ndjsonexporter",))
        )
    return redirect(reverse("users:data"))


@login_required
def sync_mastodon(request):
    if request.method == "POST":
        request.user.sync_accounts_later()
        messages.add_message(request, messages.INFO, _("Sync in progress."))
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
        messages.add_message(request, messages.INFO, _("Settings saved."))
    return redirect(reverse("users:info"))


@login_required
def import_goodreads(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not GoodreadsImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.csv", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = GoodreadsImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_storygraph(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not StoryGraphImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.csv", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = StoryGraphImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_douban(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not DoubanImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = DoubanImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        mode=int(request.POST.get("import_mode", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_letterboxd(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not LetterboxdImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = LetterboxdImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_trakt(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not TraktImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = TraktImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_opml(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))
    if not OPMLImporter.validate_file(request.FILES.get("file")):
        raise BadRequest(_("Invalid file."))
    f = (
        settings.MEDIA_ROOT
        + "/"
        + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
    )
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "wb+") as destination:
        for chunk in request.FILES["file"].chunks():
            destination.write(chunk)
    task = OPMLImporter.create(
        request.user,
        visibility=int(request.POST.get("visibility", 0)),
        mode=int(request.POST.get("import_mode", 0)),
        file=f,
    )
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


@login_required
def import_neodb(request):
    if request.method == "POST":
        format_type_hint = (
            request.POST.get("format_type", "").lower()
            if request.FILES.get("file")
            else ""
        )
        if format_type_hint == "csv":
            importer = CsvImporter
        elif format_type_hint == "ndjson":
            importer = NdjsonImporter
        else:
            raise BadRequest("Invalid file.")
        f = (
            settings.MEDIA_ROOT
            + "/"
            + GenerateDateUUIDMediaFilePath("x.zip", settings.SYNC_FILE_PATH_ROOT)
        )
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "wb+") as destination:
            for chunk in request.FILES["file"].chunks():
                destination.write(chunk)
        task = importer.create(
            request.user,
            visibility=int(request.POST.get("visibility", 0)),
            file=f,
        )
        task.enqueue()
        return redirect(reverse("users:user_task_status", args=(task.type,)))
    return redirect(reverse("users:data"))


@login_required
def import_steam(request):
    if request.method != "POST":
        return redirect(reverse("users:data"))

    # core metadatas
    metadata = copy.deepcopy(SteamImporter.DefaultMetadata)
    steam_id = (
        request.session.pop("steam_id", "") or request.POST.get("steam_id", "").strip()
    )
    metadata["steam_id"] = steam_id
    metadata["steam_api_key"] = request.POST.get("steam_api_key", "").strip()
    metadata["visibility"] = VisibilityType(int(request.POST.get("visibility", 0)))

    # config source
    source = request.POST.getlist("source[]")
    if "library" in source:
        metadata["config"]["library"].update(
            {
                "enable": True,
                "include_played_free_games": bool(
                    request.POST.get("free_filter", "played") != "no"
                ),
                "include_free_sub": bool(
                    request.POST.get("free_filter", "played") == "all"
                ),
                "playing_thresh": int(request.POST.get("playing_thresh", 2)),
                "finish_thresh": int(request.POST.get("finish_thresh", 14)),
                "last_play_to_ctime": True,  # option disabled, always true
            }
        )
    if "wishlist" in source:
        metadata["config"]["wishlist"].update({"enable": True})

    # misc config
    metadata["config"].update(
        {
            "appid_blacklist": (str(request.POST.get("ignored_appids")))
            .strip()
            .replace(" ", "")
            .split(","),
            "shelf_type_whitelist": [
                ShelfType(e)
                for e in request.POST.getlist("shelf_filters[]")
                if e in ShelfType.values
            ],
            "allow_shelf_type_reversion": bool(
                request.POST.get("allow_shelf_type_reversion", "off") == "on"
            ),
        }
    )

    task = SteamImporter.create(user=request.user, **metadata)
    task.enqueue()
    return redirect(reverse("users:user_task_status", args=(task.type,)))


# --- Authorized Apps ---


@login_required
@require_http_methods(["GET", "POST"])
def authorized_app_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        scope = request.POST.get("scope", "read")
        if not name:
            messages.error(request, _("Name is required."))
            return redirect(reverse("users:authorized_app_create"))
        identity = request.user.identity
        takahe_user = Takahe.get_identity(identity.pk).users.first()
        if not takahe_user:
            raise BadRequest("Identity not found")
        token = Takahe.create_personal_token(identity.pk, takahe_user.pk, name, scope)
        return render(
            request,
            "users/authorized_app_created.html",
            {"new_token": token},
        )
    return render(request, "users/authorized_app_create.html")


@login_required
@require_http_methods(["POST"])
def authorized_app_revoke(request):
    token_pk = request.POST.get("token_id")
    if token_pk:
        identity = request.user.identity
        Takahe.revoke_token(int(token_pk), identity.pk)
        messages.info(request, _("Application access has been revoked."))
    return redirect(reverse("users:info"))


# --- Migration ---


@login_required
@require_http_methods(["GET", "POST"])
def migrate_in(request):
    identity = request.user.identity
    tidentity = Takahe.get_identity(identity.pk)
    has_moved = Takahe.identity_has_moved(identity.pk)

    if request.method == "POST":
        if has_moved:
            messages.error(request, _("Alias update not allowed for a moved account."))
            return redirect(reverse("users:migrate_in"))

        handle = request.POST.get("alias", "").strip().lstrip("@")
        if not handle:
            messages.error(request, _("No alias specified."))
            return redirect(reverse("users:migrate_in"))

        # Resolve the handle to an identity
        target = Takahe.resolve_identity_by_handle(handle)
        if not target:
            # Try fetching remotely
            Takahe.fetch_remote_identity(handle)
            messages.info(
                request,
                _("Looking up the account. Please try again in a few seconds."),
            )
            return redirect(reverse("users:migrate_in"))

        if "remove_alias" in request.POST:
            Takahe.identity_remove_alias(identity.pk, target.actor_uri)
            messages.info(
                request, _("Alias to {handle} removed.").format(handle=target.handle)
            )
        else:
            Takahe.identity_add_alias(identity.pk, target.actor_uri)
            messages.info(
                request, _("Alias to {handle} added.").format(handle=target.handle)
            )
        return redirect(reverse("users:migrate_in"))

    aliases = Takahe.identity_get_aliases(identity.pk)
    return render(
        request,
        "users/migrate_in.html",
        {
            "aliases": aliases,
            "moved": has_moved,
            "identity": tidentity,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def migrate_out(request):
    identity = request.user.identity
    tidentity = Takahe.get_identity(identity.pk)
    has_moved = Takahe.identity_has_moved(identity.pk)

    if request.method == "POST":
        if "cancel" in request.POST:
            Takahe.identity_cancel_move(identity.pk)
            messages.info(request, _("Migration cancelled."))
            return redirect(reverse("users:migrate_out"))

        handle = request.POST.get("alias", "").strip().lstrip("@")
        if not handle:
            messages.error(request, _("No target account specified."))
            return redirect(reverse("users:migrate_out"))

        target = Takahe.resolve_identity_by_handle(handle)
        if not target:
            Takahe.fetch_remote_identity(handle)
            messages.info(
                request,
                _("Looking up the account. Please try again in a few seconds."),
            )
            return redirect(reverse("users:migrate_out"))

        if target.local:
            messages.error(request, _("Cannot migrate to a local account."))
            return redirect(reverse("users:migrate_out"))

        # Refresh remote identity to get latest aliases
        Takahe.refresh_remote_identity(target.pk)
        target.refresh_from_db()

        # Verify target has this identity as alias
        if tidentity.actor_uri not in (target.aliases or []):
            messages.error(
                request,
                _("You must set up an alias in the target account first."),
            )
            return redirect(reverse("users:migrate_out"))

        Takahe.identity_start_move(identity.pk, target.actor_uri)
        messages.info(
            request, _("Start moving to {handle}.").format(handle=target.handle)
        )
        return redirect(reverse("users:migrate_out"))

    aliases = Takahe.identity_get_aliases(identity.pk)
    return render(
        request,
        "users/migrate_out.html",
        {
            "aliases": aliases,
            "moved": has_moved,
            "identity": tidentity,
        },
    )


# --- Import/Export follow+block+mute ---


def _import_social_graph_task(
    identity_pk: int, import_type: str, entries: list[dict]
) -> None:
    """Background task to process social graph CSV import."""
    for entry in entries:
        handle = entry["handle"]
        try:
            if import_type == "following":
                InboxMessage.create_internal(
                    {
                        "type": "AddFollow",
                        "source": identity_pk,
                        "target_handle": handle,
                        "boosts": entry.get("boosts", True),
                    }
                )
            elif import_type == "blocks":
                target = Takahe.resolve_identity_by_handle(handle)
                if target:
                    Takahe.block(identity_pk, target.pk)
                else:
                    Takahe.fetch_remote_identity(handle)
            elif import_type == "mutes":
                target = Takahe.resolve_identity_by_handle(handle)
                if target:
                    Takahe.mute(identity_pk, target.pk)
                else:
                    Takahe.fetch_remote_identity(handle)
        except Exception as e:
            logger.warning(f"Error importing {import_type} for {handle}: {e}")


@login_required
@require_http_methods(["POST"])
def import_social_graph(request):
    identity = request.user.identity
    import_type = request.POST.get("import_type", "following")
    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        raise BadRequest(_("No file uploaded."))

    try:
        decoded_file = (line.decode("utf-8") for line in csv_file)
        reader = csv.DictReader(decoded_file)
        entries = []
        for row in reader:
            handle = row.get("Account address", "").strip().lstrip("@")
            if not handle or "@" not in handle:
                continue
            entry = {"handle": handle}
            if import_type == "following":
                show_boosts = row.get("Show boosts", "true").strip().lower()
                entry["boosts"] = show_boosts != "false"
            entries.append(entry)
    except (TypeError, ValueError, KeyError):
        messages.error(request, _("The uploaded file is not a valid CSV."))
        return redirect(reverse("users:info"))

    if not entries:
        messages.error(request, _("No valid entries found in the CSV file."))
        return redirect(reverse("users:info"))

    django_rq.get_queue("mastodon").enqueue(
        _import_social_graph_task, identity.pk, import_type, entries
    )
    messages.info(
        request,
        _(
            "Import of {count} entries received. They will be processed in the background."
        ).format(count=len(entries)),
    )
    return redirect(reverse("users:info"))


@login_required
def export_social_graph_csv(request, export_type: str):
    identity = request.user.identity

    response = HttpResponse(content_type="text/csv")

    if export_type == "following":
        response["Content-Disposition"] = 'attachment; filename="following.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Account address", "Show boosts", "Notify on new posts", "Languages"]
        )
        from takahe.models import Follow

        for follow in Follow.objects.filter(
            source_id=identity.pk, state="accepted"
        ).select_related("target", "target__domain"):
            writer.writerow(
                [
                    follow.target.handle,
                    "true" if follow.boosts else "false",
                    "false",
                    "",
                ]
            )
    elif export_type == "followers":
        response["Content-Disposition"] = 'attachment; filename="followers.csv"'
        writer = csv.writer(response)
        writer.writerow(["Account address"])
        from takahe.models import Follow

        for follow in Follow.objects.filter(
            target_id=identity.pk, state="accepted"
        ).select_related("source", "source__domain"):
            writer.writerow([follow.source.handle])
    elif export_type == "blocks":
        response["Content-Disposition"] = 'attachment; filename="blocked_accounts.csv"'
        writer = csv.writer(response)
        writer.writerow(["Account address"])
        from takahe.models import Block

        for block in Block.objects.filter(
            source_id=identity.pk,
            mute=False,
            state__in=["new", "sent", "awaiting_expiry"],
        ).select_related("target", "target__domain"):
            writer.writerow([block.target.handle])
    elif export_type == "mutes":
        response["Content-Disposition"] = 'attachment; filename="muted_accounts.csv"'
        writer = csv.writer(response)
        writer.writerow(["Account address", "Hide notifications"])
        from takahe.models import Block

        for block in Block.objects.filter(
            source_id=identity.pk,
            mute=True,
            state__in=["new", "sent", "awaiting_expiry"],
        ).select_related("target", "target__domain"):
            writer.writerow(
                [
                    block.target.handle,
                    "true" if block.include_notifications else "false",
                ]
            )
    else:
        raise BadRequest("Invalid export type")

    return response
