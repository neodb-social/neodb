import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponseRedirect
from django.core.exceptions import BadRequest, PermissionDenied, ObjectDoesNotExist
from django.utils import timezone
from django.contrib import messages
from .common.models import ExternalResource, IdType, IdealIdTypes
from .sites.imdb import IMDB
from .models import *
from .forms import *
from .search.views import *
from journal.models import update_journal_for_merged_item
from common.utils import get_uuid_or_404
from auditlog.context import set_actor

_logger = logging.getLogger(__name__)


def _add_error_map_detail(e):
    e.additonal_detail = []
    for f, v in e.as_data().items():
        for validation_error in v:
            if hasattr(validation_error, "error_map") and validation_error.error_map:
                for f2, v2 in validation_error.error_map.items():
                    e.additonal_detail.append(f"{f}§{f2}: {'; '.join(v2)}")
    return e


@login_required
def create(request, item_model):
    form_cls = CatalogForms.get(item_model)
    if not form_cls:
        raise BadRequest("Invalid item type")
    if request.method == "GET":
        form = form_cls(
            initial={
                "title": request.GET.get("title", ""),
            }
        )
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
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            if parent:
                form.instance.set_parent_item(parent)
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))
    else:
        raise BadRequest("Invalid request method")


@login_required
def history(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        return render(request, "catalog_history.html", {"item": item})
    else:
        raise BadRequest()


@login_required
def edit(request, item_path, item_uuid):
    if request.method == "GET":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(instance=item)
        if (
            item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        return render(request, "catalog_edit.html", {"form": form, "item": item})
    elif request.method == "POST":
        item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
        form_cls = CatalogForms[item.__class__.__name__]
        form = form_cls(request.POST, request.FILES, instance=item)
        if (
            item.external_resources.all().count() > 0
            and item.primary_lookup_id_value
            and item.primary_lookup_id_type in IdealIdTypes
        ):
            form.fields["primary_lookup_id_type"].disabled = True
            form.fields["primary_lookup_id_value"].disabled = True
        if form.is_valid():
            form.instance.last_editor = request.user
            form.instance.edited_time = timezone.now()
            form.instance.save()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))
    else:
        raise BadRequest()


@login_required
def delete(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff and item.journal_exists():
        raise PermissionDenied()
    item.delete()
    return (
        redirect(item.url + "?skipcheck=1") if request.user.is_staff else redirect("/")
    )


@login_required
def recast(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
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
    _logger.warn(f"{request.user} recasting {item} to {model}")
    if isinstance(item, TVShow):
        for season in item.seasons.all():
            _logger.warn(f"{request.user} recast orphaning season {season}")
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


@login_required
def unlink(request):
    if request.method != "POST":
        raise BadRequest()
    if not request.user.is_staff:
        raise PermissionDenied()
    res_id = request.POST.get("id")
    if not res_id:
        raise BadRequest()
    resource = get_object_or_404(ExternalResource, id=res_id)
    resource.unlink_from_item()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


@login_required
def assign_parent(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    parent_item = Item.get_by_url(request.POST.get("parent_item_url"))
    if parent_item:
        if parent_item.is_deleted or parent_item.merged_to_item_id:
            raise BadRequest("Can't assign parent to a deleted or redirected item")
        if parent_item.child_class != item.__class__.__name__:
            raise BadRequest("Incompatible child item type")
    # if not request.user.is_staff and item.parent_item:
    #     raise BadRequest("Already assigned to a parent item")
    _logger.warn(f"{request.user} assign {item} to {parent_item}")
    item.set_parent_item(parent_item)
    item.save()
    return redirect(item.url)


@login_required
def remove_unused_seasons(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    l = list(item.seasons.all())
    for s in l:
        if not s.journal_exists():
            s.delete()
    l = [s.id for s in l]
    l2 = [s.id for s in item.seasons.all()]
    item.log_action({"!remove_unused_seasons": [l, l2]})
    return redirect(item.url)


@login_required
def fetch_tvepisodes(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.class_name != "tvseason" or not item.imdb or item.season_number is None:
        raise BadRequest()
    item.log_action({"!fetch_tvepisodes": ["", ""]})
    django_rq.get_queue("crawl").enqueue(
        fetch_episodes_for_season_task, item.uuid, request.user
    )
    messages.add_message(request, messages.INFO, _("已开始更新单集信息"))
    return redirect(item.url)


def fetch_episodes_for_season_task(item_uuid, user):
    with set_actor(user):
        season = Item.get_by_url(item_uuid)
        episodes = season.episode_uuids
        IMDB.fetch_episodes_for_season(season)
        season.log_action({"!fetch_tvepisodes": [episodes, season.episode_uuids]})


@login_required
def merge(request, item_path, item_uuid):
    if request.method != "POST":
        raise BadRequest()
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if not request.user.is_staff and item.journal_exists():
        raise PermissionDenied()
    if request.POST.get("new_item_url"):
        new_item = Item.get_by_url(request.POST.get("new_item_url"))
        if not new_item or new_item.is_deleted or new_item.merged_to_item_id:
            raise BadRequest(_("不能合并到一个被删除或合并过的条目。"))
        if new_item.class_name != item.class_name:
            raise BadRequest(
                _("不能合并不同类的条目") + f" ({item.class_name} to {new_item.class_name})"
            )
        _logger.warn(f"{request.user} merges {item} to {new_item}")
        item.merge_to(new_item)
        update_journal_for_merged_item(item_uuid)
        return redirect(new_item.url)
    else:
        if item.merged_to_item:
            _logger.warn(f"{request.user} cancels merge for {item}")
            item.merge_to(None)
        return redirect(item.url)
