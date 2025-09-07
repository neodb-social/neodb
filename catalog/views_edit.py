from auditlog.context import set_actor
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models.lang import get_current_locales
from common.utils import discord_send, get_uuid_or_404
from journal.models import update_journal_for_merged_item_task

from .forms import *
from .models import ExternalResource, IdealIdTypes, IdType
from .search.views import *
from .sites.imdb import IMDB as IMDB


def _add_error_map_detail(e):
    e.additonal_detail = []
    for f, v in e.as_data().items():
        for validation_error in v:
            if hasattr(validation_error, "error_map") and validation_error.error_map:
                for f2, v2 in validation_error.error_map.items():
                    e.additonal_detail.append(f"{f}§{f2}: {'; '.join(v2)}")
    return e


@require_http_methods(["GET", "POST"])
@login_required
def create(request, item_model):
    form_cls = CatalogForms.get(item_model)
    if not form_cls:
        raise BadRequest("Invalid item type")
    initial = {}
    t = request.GET.get("title", "")
    if t:
        initial = {
            "localized_title": [{"text": t, "lang": get_current_locales()[0]}],
        }
    if request.method == "GET":
        form = form_cls(initial=initial)
        return render(
            request,
            "catalog_edit.html",
            {
                "form": form,
            },
        )
    elif request.method == "POST":
        form = form_cls(request.POST, request.FILES)
        parent = None
        if request.GET.get("parent", ""):
            parent = get_object_or_404(
                Item, uid=get_uuid_or_404(request.GET.get("parent", ""))
            )
            if parent.child_class != form.instance.__class__.__name__:
                raise BadRequest(
                    f"Invalid parent type: {form.instance.__class__} -> {parent.__class__}"
                )
        if form.is_valid():
            form.instance.edited_time = timezone.now()
            if parent:
                form.instance.set_parent_item(parent)
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))
    else:
        raise BadRequest("Invalid request method")


@require_http_methods(["GET"])
@login_required
def history(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    return render(request, "catalog_history.html", {"item": item})


@require_http_methods(["GET", "POST"])
@login_required
def edit(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        if item.is_protected and not request.user.is_staff:
            raise PermissionDenied(_("Editing this item is restricted."))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(instance=item)
        if (
            not request.user.is_staff
            and item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        return render(request, "catalog_edit.html", {"form": form, "item": item})
    else:
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        if item.is_protected and not request.user.is_staff:
            raise PermissionDenied(_("Editing this item is restricted."))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(request.POST, request.FILES, instance=item)
        if (
            not request.user.is_staff
            and item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        if form.is_valid():
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))


@require_http_methods(["POST"])
@login_required
def delete(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    if not request.user.is_staff and item.journal_exists():
        raise PermissionDenied(_("Item in use."))
    if not item.is_deletable():
        raise PermissionDenied(_("Item cannot be deleted."))
    if request.POST.get("sure", 0) != "1":
        return render(request, "catalog_delete.html", {"item": item})
    else:
        item.delete()
        discord_send(
            "audit",
            f"{item.absolute_url}?skipcheck=1\nby [@{request.user.username}]({request.user.absolute_url})",
            thread_name=f"[delete] {item.display_title}",
            username=f"@{request.user.username}",
        )
        return (
            redirect(item.url + "?skipcheck=1")
            if request.user.is_staff
            else redirect("/")
        )


@require_http_methods(["POST"])
@login_required
def undelete(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff:
        raise PermissionDenied(_("Insufficient permission"))
    item.is_deleted = False
    item.save()
    return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def recast(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    cls = request.POST.get("class")
    # TODO move some of the logic to model
    douban_movie_to_tvseason = False
    if cls == "tvshow":
        if item.external_resources.filter(id_type=IdType.DoubanMovie).exists():
            cls = "tvseason"
            douban_movie_to_tvseason = True
    model = (
        TVShow
        if cls == "tvshow"
        else (Movie if cls == "movie" else (TVSeason if cls == "tvseason" else None))
    )
    if not model:
        raise BadRequest("Invalid target type")
    if isinstance(item, model):
        raise BadRequest("Same target type")
    logger.warning(f"{request.user} recasting {item} to {model}")
    discord_send(
        "audit",
        f"{item.absolute_url}\n{item.__class__.__name__} ➡ {model.__name__}\nby [@{request.user.username}]({request.user.absolute_url})",
        thread_name=f"[recast] {item.display_title}",
        username=f"@{request.user.username}",
    )
    if isinstance(item, TVShow):
        for season in item.seasons.all():
            logger.warning(f"{request.user} recast orphaning season {season}")
            season.show = None
            season.save(update_fields=["show"])
    new_item = item.recast_to(model)
    if douban_movie_to_tvseason:
        for res in item.external_resources.filter(
            id_type__in=[IdType.IMDB, IdType.TMDB_TV]
        ):
            res.item = None
            res.save(update_fields=["item"])
    return redirect(new_item.url)


@require_http_methods(["POST"])
@login_required
def unlink(request):
    if not request.user.is_staff:
        raise PermissionDenied(_("Insufficient permission"))
    res_id = request.POST.get("id")
    if not res_id:
        raise BadRequest(_("Invalid parameter"))
    resource = get_object_or_404(ExternalResource, id=res_id)
    if not resource.item:
        raise BadRequest(_("Invalid parameter"))
    resource.unlink_from_item()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@require_http_methods(["POST"])
@login_required
def assign_parent(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    parent_item = Item.get_by_url(request.POST.get("parent_item_url"))
    if parent_item:
        if parent_item.is_protected and not request.user.is_staff:
            raise PermissionDenied(_("Editing this item is restricted."))
        if parent_item.is_deleted or parent_item.merged_to_item_id:
            raise BadRequest("Can't assign parent to a deleted or redirected item")
        if parent_item.child_class != item.__class__.__name__:
            raise BadRequest("Incompatible child item type")
    # if not request.user.is_staff and item.parent_item:
    #     raise BadRequest("Already assigned to a parent item")
    logger.warning(f"{request.user} assign {item} to {parent_item}")
    item.set_parent_item(parent_item)
    item.save()
    return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def remove_unused_seasons(request, item_path, item_uuid):
    item = get_object_or_404(TVShow, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    sl = list(item.seasons.all())
    for s in sl:
        if not s.journal_exists():
            s.delete()
    ol = [s.pk for s in sl]
    nl = [s.pk for s in item.seasons.all()]
    discord_send(
        "audit",
        f"{item.absolute_url}\n{ol} ➡ {nl}\nby [@{request.user.username}]({request.user.absolute_url})",
        thread_name=f"[cleanup] {item.display_title}",
        username=f"@{request.user.username}",
    )
    item.log_action({"!remove_unused_seasons": [ol, nl]})
    return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def fetch_tvepisodes(request, item_path, item_uuid):
    item = get_object_or_404(TVSeason, uid=get_uuid_or_404(item_uuid))
    if item.class_name != "tvseason" or not item.imdb or item.season_number is None:
        raise BadRequest(_("TV Season with IMDB id and season number required."))
    item.log_action({"!fetch_tvepisodes": ["", ""]})
    django_rq.get_queue("crawl").enqueue(
        fetch_episodes_for_season_task, item.uuid, request.user
    )
    messages.add_message(request, messages.INFO, _("Updating episodes"))
    return redirect(item.url)


def fetch_episodes_for_season_task(item_uuid, user):
    with set_actor(user):
        season = TVSeason.get_by_url(item_uuid)
        if not season:
            return
        episodes = season.episode_uuids
        IMDB.fetch_episodes_for_season(season)
        season.log_action({"!fetch_tvepisodes": [episodes, season.episode_uuids]})


@require_http_methods(["POST"])
@login_required
def merge(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    if not request.user.is_staff and item.journal_exists():
        raise PermissionDenied(_("Insufficient permission"))
    if request.POST.get("sure", 0) != "1":
        new_item = Item.get_by_url(request.POST.get("target_item_url"))
        return render(
            request,
            "catalog_merge.html",
            {"item": item, "new_item": new_item, "mode": "merge"},
        )
    elif request.POST.get("target_item_url"):
        new_item = Item.get_by_url(request.POST.get("target_item_url"))
        if not new_item or new_item.is_deleted or new_item.merged_to_item_id:
            raise BadRequest(_("Cannot be merged to an item already deleted or merged"))
        if new_item.is_protected and not request.user.is_staff:
            raise PermissionDenied(_("Editing this item is restricted."))
        if new_item.class_name != item.class_name:
            raise BadRequest(
                _("Cannot merge items in different categories")
                + f" ({item.class_name} to {new_item.class_name})"
            )
        if new_item == item:
            raise BadRequest(_("Cannot merge an item to itself"))
        logger.warning(f"{request.user} merges {item} to {new_item}")
        item.merge_to(new_item)
        django_rq.get_queue("crawl").enqueue(
            update_journal_for_merged_item_task, request.user.pk, item.uuid
        )
        discord_send(
            "audit",
            f"{item.absolute_url}?skipcheck=1\n⬇\n{new_item.absolute_url}\nby [@{request.user.username}]({request.user.absolute_url})",
            thread_name=f"[merge] {item.display_title}",
            username=f"@{request.user.username}",
        )
        return redirect(new_item.url)
    else:
        if item.merged_to_item:
            logger.warning(f"{request.user} cancels merge for {item}")
            item.merge_to(None)
        discord_send(
            "audit",
            f"{item.absolute_url}\n⬇\n(none)\nby [@{request.user.username}]({request.user.absolute_url})",
            thread_name=f"[merge] {item.display_title}",
            username=f"@{request.user.username}",
        )
        return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def link_edition(request, item_path, item_uuid):
    item = get_object_or_404(Edition, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    new_item = Edition.get_by_url(request.POST.get("target_item_url"))
    if (
        not new_item
        or new_item.is_deleted
        or new_item.merged_to_item_id
        or item == new_item
    ):
        raise BadRequest(_("Cannot be linked to an item already deleted or merged"))
    if new_item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    if item.class_name != "edition" or new_item.class_name != "edition":
        raise BadRequest(_("Cannot link items other than editions"))
    if request.POST.get("sure", 0) != "1":
        new_item = Edition.get_by_url(request.POST.get("target_item_url"))
        return render(
            request,
            "catalog_merge.html",
            {"item": item, "new_item": new_item, "mode": "link"},
        )
    logger.warning(f"{request.user} merges {item} to {new_item}")
    item.link_to_related_book(new_item)
    discord_send(
        "audit",
        f"{item.absolute_url}?skipcheck=1\n⬇\n{new_item.absolute_url}\nby [@{request.user.username}]({request.user.absolute_url})",
        thread_name=f"[link edition] {item.display_title}",
        username=f"@{request.user.username}",
    )
    return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def unlink_works(request, item_path, item_uuid):
    item = get_object_or_404(Edition, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    if not request.user.is_staff and item.journal_exists():
        raise PermissionDenied(_("Insufficient permission"))
    item.set_parent_item(None)
    discord_send(
        "audit",
        f"{item.absolute_url}?skipcheck=1\nby [@{request.user.username}]({request.user.absolute_url})",
        thread_name=f"[unlink works] {item.display_title}",
        username=f"@{request.user.username}",
    )
    return (
        redirect(item.url + "?skipcheck=1") if request.user.is_staff else redirect("/")
    )


@require_http_methods(["POST"])
@login_required
def suggest(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not discord_send(
        "suggest",
        f"{item.absolute_url}\n> {request.POST.get('detail', '<none>')}\nby [@{request.user.username}]({request.user.absolute_url})",
        thread_name=f"[{request.POST.get('action', 'none')}] {item.display_title}",
        username=f"@{request.user.username}",
    ):
        raise Http404("Discord webhook not configured")
    return redirect(item.url)


@require_http_methods(["POST"])
@login_required
def protect(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff:
        raise PermissionDenied(_("Insufficient permission"))
    item.is_protected = bool(request.POST.get("protected"))
    item.save()
    return redirect(item.url)
