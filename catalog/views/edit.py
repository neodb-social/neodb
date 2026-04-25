import django_rq
from auditlog.context import set_actor
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models.lang import get_current_locales
from common.utils import discord_send, get_uuid_or_404
from common.validators import get_safe_referer_url
from journal.models import update_journal_for_merged_item_task
from users.models import User

from ..forms import CatalogForms
from ..jobs import people_works
from ..models import (
    Edition,
    ExternalResource,
    IdealIdTypes,
    IdType,
    Item,
    ItemCredit,
    Movie,
    TVSeason,
    TVShow,
)
from ..models.people import People
from ..sites import IMDB

fetch_works_for_person_task = people_works.fetch_works_for_person_task


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

    if request.method == "POST":
        form = form_cls(request.POST, request.FILES)
    else:
        initial = {}
        t = request.GET.get("title", "")
        if item_model == "People":
            if t:
                initial["localized_name"] = [
                    {"text": t, "lang": get_current_locales()[0]}
                ]
            pt = request.GET.get("people_type", "")
            if pt in ("person", "organization"):
                initial["people_type"] = pt
        elif t:
            initial["localized_title"] = [{"text": t, "lang": get_current_locales()[0]}]
        form = form_cls(initial=initial)

    if request.method == "POST":
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
            form.instance.sync_credits_from_metadata()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))

    return render(
        request,
        "catalog_edit.html",
        {
            "form": form,
        },
    )


@require_http_methods(["GET"])
@login_required
def history(request, item_path, item_uuid):
    from auditlog.models import LogEntry
    from django.contrib.contenttypes.models import ContentType

    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    # Include ItemCredit audit log entries for this item.
    # Create/delete entries have 'item' in changes; update entries may not.
    # First collect all credit object_ids that reference this item, then
    # fetch ALL log entries for those object_ids (including updates).
    credit_ct = ContentType.objects.get_for_model(ItemCredit)
    current_ids = set(str(pk) for pk in item.credits.values_list("pk", flat=True))
    deleted_ids = set(
        LogEntry.objects.filter(
            content_type=credit_ct,
            changes__item__contains=[str(item.pk)],
        ).values_list("object_id", flat=True)
    )
    all_credit_ids = current_ids | deleted_ids
    credit_logs = LogEntry.objects.filter(
        content_type=credit_ct, object_id__in=all_credit_ids
    )
    all_logs = (item.history.all() | credit_logs).order_by("-timestamp")
    return render(
        request, "catalog_history.html", {"item": item, "history_logs": all_logs}
    )


@require_http_methods(["GET", "POST"])
@login_required
def edit(request, item_path, item_uuid):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))

    form_cls = CatalogForms[item.__class__.__name__]

    if request.method == "POST":
        form = form_cls(request.POST, request.FILES, instance=item)
    else:
        form = form_cls(instance=item)

    if (
        not request.user.is_staff
        and item.external_resources.all().count() > 0
        and item.primary_lookup_id_value
        and item.primary_lookup_id_type in IdealIdTypes
    ):
        form.fields["primary_lookup_id_type"].disabled = True
        form.fields["primary_lookup_id_value"].disabled = True

    if request.method == "POST":
        if form.is_valid():
            form.instance.edited_time = timezone.now()
            form.instance.save()
            form.instance.sync_credits_from_metadata()
            return redirect(form.instance.url)
        else:
            raise BadRequest(_add_error_map_detail(form.errors))

    people_names: dict[str, str] = {}
    if hasattr(item, "credits"):
        for credit in item.credits.select_related("person").filter(
            person__isnull=False
        ):
            people_names[credit.person.uuid] = credit.person.display_name
    return render(
        request,
        "catalog_edit.html",
        {
            "form": form,
            "item": item,
            "people_names_json": people_names,
            "people_works_source_ids": people_works.supported_people_work_source_ids(),
        },
    )


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
    return HttpResponseRedirect(get_safe_referer_url(request, "/"))


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
        fetch_episodes_for_season_task, item.uuid, request.user.pk
    )
    messages.add_message(request, messages.INFO, _("Updating episodes"))
    return redirect(item.url)


def fetch_episodes_for_season_task(item_uuid, user_id):
    user = User.objects.filter(pk=user_id).first() if user_id else None
    with set_actor(user):
        season = TVSeason.get_by_url(item_uuid)
        if not season:
            return
        episodes = season.episode_uuids
        IMDB.fetch_episodes_for_season(season)
        season.log_action({"!fetch_tvepisodes": [episodes, season.episode_uuids]})


@require_http_methods(["POST"])
@login_required
def fetch_people_works(request, item_path, item_uuid):
    item = get_object_or_404(People, uid=get_uuid_or_404(item_uuid))
    if item.is_protected and not request.user.is_staff:
        raise PermissionDenied(_("Editing this item is restricted."))
    resource = people_works.get_people_works_resource(
        item, request.POST.get("resource_id")
    )
    if not resource:
        raise BadRequest(_("This person has no supported source to fetch works from."))
    lock_key = f"_fetch_works_lock:{item.pk}"
    if not cache.add(lock_key, 1, timeout=people_works.FETCH_PEOPLE_WORKS_LOCK_TTL):
        messages.add_message(
            request,
            messages.WARNING,
            _("Already pulling works for this person, try again later."),
        )
        return redirect(item.url)
    people_works.enqueue_people_works(item, request.user, resource)
    messages.add_message(request, messages.INFO, _("Pulling works in background."))
    return redirect(item.url)


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


@require_http_methods(["GET"])
@login_required
def item_credits(request, item_path, item_uuid):
    """Render the credits list for an item (HTMX partial)."""
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    credits = item.credits.select_related("person").all()
    return render(
        request,
        "_item_credits_list.html",
        {
            "item": item,
            "credits": credits,
            "credit_roles": type(item).credit_role_choices(),
        },
    )


@require_http_methods(["POST"])
@login_required
def add_credit(request, item_path, item_uuid):
    """Add a credit to an item. Accepts name or People URL."""
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    role = request.POST.get("role", "")
    name_input = request.POST.get("name", "").strip()
    if not role or not name_input:
        raise BadRequest("role and name are required")
    allowed_roles = {v for v, _ in type(item).credit_role_choices()}
    if role not in allowed_roles:
        raise BadRequest("invalid role")

    person = None
    name = name_input

    # Check if input is a People URL (e.g., /people/xxxx or full URL)
    if "/people/" in name_input:
        uuid_part = name_input.rstrip("/").split("/people/")[-1].split("/")[0]
        try:
            uid = get_uuid_or_404(uuid_part)
            person = People.objects.filter(uid=uid).first()
        except Http404:
            person = None
        if person:
            name = person.display_name

    # If not a URL, search for matching People by name
    if not person:
        matches = People.find_by_name(name)
        if len(matches) == 1:
            person = matches[0]

    order = item.credits.count()
    ItemCredit.objects.get_or_create(
        item=item,
        role=role,
        name=name,
        defaults={"person": person, "order": order},
    )
    credits = item.credits.select_related("person").all()
    return render(
        request,
        "_item_credits_list.html",
        {
            "item": item,
            "credits": credits,
            "credit_roles": type(item).credit_role_choices(),
        },
    )


@require_http_methods(["POST"])
@login_required
def remove_credit(request, item_path, item_uuid, credit_id):
    """Remove a credit from an item."""
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    credit = get_object_or_404(ItemCredit, pk=credit_id, item=item)
    credit.delete()
    credits = item.credits.select_related("person").all()
    return render(
        request,
        "_item_credits_list.html",
        {
            "item": item,
            "credits": credits,
            "credit_roles": type(item).credit_role_choices(),
        },
    )


@require_http_methods(["POST"])
@login_required
def update_credit(request, item_path, item_uuid, credit_id):
    """Update a credit's character name."""
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    credit = get_object_or_404(ItemCredit, pk=credit_id, item=item)
    character_name = request.POST.get("character_name", "").strip()
    credit.character_name = character_name
    credit.save()
    credits = item.credits.select_related("person").all()
    return render(
        request,
        "_item_credits_list.html",
        {
            "item": item,
            "credits": credits,
            "credit_roles": type(item).credit_role_choices(),
        },
    )


@require_http_methods(["GET"])
@login_required
def search_people(request):
    """Search People by name for autocomplete (returns JSON)."""
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    try:
        people = People.find_by_name(q, exact=False, limit=10)
    except RuntimeError as e:
        logger.error(f"search_people index error: {e}")
        return JsonResponse({"error": "search_unavailable"}, status=503)
    results = [
        {"url": p.url, "name": p.display_name, "cover": p.cover_image_url}
        for p in people
    ]
    return JsonResponse(results, safe=False)
