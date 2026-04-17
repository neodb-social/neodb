import re
import time
import traceback
from datetime import timedelta
from time import sleep

import django_rq
from discord import Object, SyncWebhook
from django.db import connection, models
from django.utils import timezone
from loguru import logger
from rq.job import Job
from tqdm import tqdm

from common.models.site_config import SiteConfig

_CHAIN_KEY = "neodb:migration_enqueue:last_job_id"
_CHAIN_TTL = 7 * 24 * 3600
_DATE_SUFFIX_RE = re.compile(r"_\d{8}$")
_NOTIFY_CHANNEL = "system"
_DEFAULT_HEAD_DELAY = timedelta(seconds=5)
_NO_DELAY = object()


def _derive_skip_key(func) -> str:
    return _DATE_SUFFIX_RE.sub("", func.__name__)


def _make_migration_notifier(skip_key: str):
    """Return a notify(content) callable that posts all messages for one
    migration into the same Discord thread. The first call creates the
    thread via thread_name; subsequent calls reuse the captured thread id.
    The configured `system` (or `default`) webhook MUST target a Discord
    forum or media channel -- regular text channels reject thread_name and
    all notifications for the run will fail. Errors are logged and swallowed
    so notifications never break a run.
    """
    dw = SiteConfig.system.discord_webhooks.get(
        _NOTIFY_CHANNEL
    ) or SiteConfig.system.discord_webhooks.get("default")
    webhook = None
    if dw:
        try:
            webhook = SyncWebhook.from_url(dw)
        except Exception as e:
            logger.warning(f"[migration] {skip_key}: discord webhook init failed: {e}")
    state: dict = {"thread_id": None}

    def notify(content: str) -> None:
        if webhook is None:
            return
        try:
            if state["thread_id"] is None:
                msg = webhook.send(content[:1989], thread_name=skip_key[:99], wait=True)
                state["thread_id"] = msg.channel.id
            else:
                webhook.send(content[:1989], thread=Object(id=state["thread_id"]))
        except Exception as e:
            logger.warning(f"[migration] {skip_key}: discord notify failed: {e}")

    return notify


def _run_migration_job(func, skip_key, args, kwargs):
    SiteConfig.ensure_loaded()
    notify = _make_migration_notifier(skip_key)
    if skip_key in (SiteConfig.system.skip_migrations or []):
        logger.warning(f"[migration] {skip_key}: skipped (skip_migrations)")
        notify(f"[migration] {skip_key}: skipped")
        return None
    notify(f"[migration] {skip_key}: started")
    t0 = time.monotonic()
    try:
        result = func(*args, **(kwargs or {}))
    except Exception:
        dt = time.monotonic() - t0
        tb = traceback.format_exc()
        notify(
            f"[migration] {skip_key}: FAILED after {dt:.0f}s\n```\n{tb[-1500:]}\n```"
        )
        raise
    dt = time.monotonic() - t0
    notify(f"[migration] {skip_key}: finished in {dt:.0f}s")
    return result


def enqueue_migration_job(
    func,
    *,
    skip_key: str | None = None,
    queue: str = "cron",
    delay=_NO_DELAY,
    args: tuple = (),
    kwargs: dict | None = None,
):
    """Enqueue a post-migration background job, chained after any previously
    enqueued migration job so they run sequentially even on multi-worker queues.
    The worker-side wrapper posts begin/end/failure to the Discord 'system'
    channel, and honours SiteConfig.system.skip_migrations at dequeue time so
    the skip list can be toggled from the Admin > Advanced UI without a
    restart. When this is the first job in a chain, a default 5-second delay
    lets the enclosing migrate transaction commit before the worker picks up.
    """
    key = skip_key or _derive_skip_key(func)

    # Migrations run inside an atomic block; django_rq's default
    # commit_mode='on_db_commit' would defer enqueue to transaction.on_commit
    # and return None, breaking the chain. Force 'auto' so we get the Job back.
    q = django_rq.get_queue(queue, commit_mode="auto")
    conn = q.connection
    depends_on = None

    last_id = conn.get(_CHAIN_KEY)
    if last_id is not None:
        if isinstance(last_id, bytes):
            last_id = last_id.decode()
        if isinstance(last_id, str):
            try:
                prev = Job.fetch(last_id, connection=conn)
                if prev.get_status() in ("queued", "scheduled", "deferred", "started"):
                    depends_on = prev
            except Exception:
                depends_on = None

    job_args = (func, key, tuple(args), dict(kwargs or {}))
    enqueue_kwargs: dict = {"args": job_args}
    if depends_on is not None:
        enqueue_kwargs["depends_on"] = depends_on

    if delay is _NO_DELAY:
        effective_delay = _DEFAULT_HEAD_DELAY if depends_on is None else None
    else:
        effective_delay = delay

    if depends_on is None and isinstance(effective_delay, timedelta):
        job = q.enqueue_in(effective_delay, _run_migration_job, **enqueue_kwargs)
    else:
        job = q.enqueue(_run_migration_job, **enqueue_kwargs)

    conn.set(_CHAIN_KEY, job.id, ex=_CHAIN_TTL)
    print(f"(Queued {key})", end="")
    return job


def fix_20250208():
    logger.warning("Fixing soft-deleted editions...")
    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE catalog_item
            SET is_deleted = true
            WHERE id NOT IN ( SELECT item_ptr_id FROM catalog_edition ) AND polymorphic_ctype_id = (SELECT id FROM django_content_type WHERE app_label='catalog' AND model='edition');
            INSERT INTO catalog_edition (item_ptr_id)
            SELECT id FROM catalog_item
            WHERE id NOT IN ( SELECT item_ptr_id FROM catalog_edition ) AND polymorphic_ctype_id = (SELECT id FROM django_content_type WHERE app_label='catalog' AND model='edition');
        """)
    logger.warning("Fix complete.")


def merge_works_20250301():
    from catalog.models import Edition, Work

    logger.warning("Start merging works...")
    editions = Edition.objects.annotate(n=models.Count("works")).filter(n__gt=1)
    primary_work = []
    merge_map = {}
    for edition in tqdm(editions):
        w = Work.objects.filter(
            editions=edition, is_deleted=False, merged_to_item__isnull=True
        ).first()
        if w is None:
            logger.error(f"No active work found for {edition}")
            continue
        merge_to_id = w.pk
        if merge_to_id in merge_map:
            merge_to_id = merge_map[merge_to_id]
        elif merge_to_id not in primary_work:
            primary_work.append(merge_to_id)
        for work in Work.objects.filter(editions=edition).exclude(pk=w.pk):
            if work.pk in merge_map:
                if merge_map[work.pk] != merge_to_id:
                    logger.error(
                        f"{Work.objects.get(pk=merge_to_id)} and {Work.objects.get(pk=merge_map[work.pk])} might need to be merged manually."
                    )
            elif work.pk in primary_work:
                logger.error(
                    f"{Work.objects.get(pk=merge_to_id)} and {work} might need to be merged manually."
                )
            else:
                merge_map[work.pk] = merge_to_id

    logger.warning(
        f"{len(primary_work)} primay work total, and {len(merge_map)} merges will be processed."
    )
    for k, v in tqdm(merge_map.items()):
        from_work = Work.objects.get(pk=k)
        to_work = Work.objects.get(pk=v)
        # print(from_work, '->', to_work)
        from_work.merge_to(to_work)
        for edition in from_work.editions.all():
            # doing this as work.merge_to() may miss edition belonging to both from and to
            from_work.editions.remove(edition)
            to_work.editions.add(edition)

    logger.warning("Applying unique index...")
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS catalog_work_editions_work_id_uniq ON catalog_work_editions (edition_id);
            """)

    logger.warning("Merging works completed.")


def fix_bangumi_20250420():
    from catalog.models import Item

    logger.warning("Scaning catalog for issues.")
    fixed = 0
    for i in Item.objects.all().iterator():
        changed = False
        for a in ["location", "director", "language"]:
            v = getattr(i, a, None)
            if isinstance(v, str):
                setattr(i, a, v.split("、"))
                changed = True
        v = getattr(i, "pub_house", None)
        if isinstance(v, list):
            setattr(i, "pub_house", "/".join(v))
            changed = True
        if changed:
            i.save(update_fields=["metadata"])
            fixed += 1
    logger.warning(f"{fixed} items fixed.")


def reindex_20250424():
    from django.core.paginator import Paginator

    from catalog.models import Item
    from catalog.search import CatalogIndex

    logger.warning("Checking index status.")
    index = CatalogIndex.instance()
    s = index.initialize_collection(max_wait=30)
    if not s:
        logger.error("Index is not ready, reindexing aborted.")
        return
    logger.warning("Reindexing started.")
    items = Item.objects.filter(
        is_deleted=False, merged_to_item_id__isnull=True
    ).order_by("id")
    c = 0
    t = 0
    pg = Paginator(items, 1000)
    for p in tqdm(pg.page_range):
        docs = index.items_to_docs(pg.get_page(p).object_list)
        r = index.replace_docs(docs)
        t += len(docs)
        c += r
    logger.warning(f"Reindexing complete: updated {c} of {t} docs.")


def normalize_language_20250524():
    from catalog.models import Item
    from common.models.lang import normalize_languages

    logger.warning("normalize_language start")
    c = Item.objects.all().count()
    u = 0
    for i in tqdm(Item.objects.all().iterator(), total=c):
        lang = getattr(i, "language", None)
        if lang:
            lang2 = normalize_languages(lang)
            if lang2 != lang:
                setattr(i, "language", lang2)
                i.save(update_fields=["metadata"])
                u += 1
    logger.warning(f"normalize_language finished. {u} of {c} items updated.")


def normalize_genre_20260412():
    from catalog.models import Item
    from common.models.genre import normalize_genres

    logger.warning("normalize_genre start")
    c = Item.objects.all().count()
    u = 0
    for i in tqdm(Item.objects.all().iterator(), total=c):
        genre = getattr(i, "genre", None)
        if genre:
            if isinstance(genre, str):
                genre = [genre]
            genre2 = normalize_genres(genre)
            if genre2 != genre:
                setattr(i, "genre", genre2)
                i.save(update_fields=["metadata"])
                u += 1
    logger.warning(f"normalize_genre finished. {u} of {c} items updated.")


def link_tmdb_wikidata_20250815(limit=None):
    """
    Scan all TMDB Movie and TVShow resources, refetch them, and link to WikiData resources if available.

    This function:
    1. Finds all ExternalResources with TMDB Movie and TVShow ID types
    2. Refetches each TMDB resource to ensure we have the latest data
    3. If the TMDB resource has a WikiData ID, fetches the corresponding WikiData resource
    4. Links both resources to the same Item
    """
    from catalog.common import SiteManager
    from catalog.models import ExternalResource, IdType
    from catalog.sites.wikidata import WikiData

    logger.warning("Starting TMDB-WikiData linking process")
    tmdb_resources = ExternalResource.objects.filter(
        id_type__in=[IdType.TMDB_Movie, IdType.TMDB_TV]
    )
    if limit:
        tmdb_resources = tmdb_resources[:limit]
    count_total = tmdb_resources.count()
    count_with_wikidata = 0
    count_errors = 0
    count_success = 0
    logger.warning(f"Found {count_total} TMDB resources to process")
    for resource in tqdm(tmdb_resources, total=count_total):
        try:
            site_cls = SiteManager.get_site_cls_by_id_type(resource.id_type)
            if not site_cls:
                logger.error(f"Could not find site class for {resource.id_type}")
                count_errors += 1
                continue
            site = site_cls(resource.url)
            try:
                resource_content = site.scrape()
            except Exception as e:
                logger.error(f"Failed to scrape {resource.url}: {e}")
                count_errors += 1
                continue
            wikidata_id = resource_content.lookup_ids.get(IdType.WikiData)
            if not wikidata_id:
                continue
            resource.update_content(resource_content)
            count_with_wikidata += 1
            wiki_site = WikiData(id_value=wikidata_id)
            try:
                wiki_site.get_resource_ready()
                count_success += 1
            except Exception as e:
                logger.error(
                    f"Failed to process WikiData {e}", extra={"qid": wikidata_id}
                )
                count_errors += 1
            sleep(0.5)
        except Exception as e:
            logger.error(f"Error processing resource {resource}: {e}")
            count_errors += 1
    logger.warning("TMDB-WikiData linking process completed:")
    logger.warning(f"  Total TMDB resources processed: {count_total}")
    logger.warning(f"  TMDB resources with WikiData IDs: {count_with_wikidata}")
    logger.warning(f"  Errors encountered: {count_errors}")
    return {
        "total": count_total,
        "with_wikidata": count_with_wikidata,
        "errors": count_errors,
        "success": count_success,
    }


def fix_missing_cover_20250821(days=0):
    from catalog.models import Item, PodcastEpisode, item_content_types

    updated = 0
    ct = item_content_types()[PodcastEpisode]
    items = Item.objects.filter(cover="item/default.svg").exclude(
        polymorphic_ctype_id=ct
    )
    if days:
        time_threshold = timezone.now() - timedelta(days=days)
        items = items.filter(edited_time__gt=time_threshold)
    for i in tqdm(items):
        p = (
            i.external_resources.exclude(cover="item/default.svg")
            .exclude(cover__isnull=True)
            .first()
        )
        if p and p.has_cover():
            i.cover = p.cover
            i.save()
            updated += 1
    logger.success(f"{updated} items with missing covers has been fixed.")


def populate_credits_20260412(start_pk=0, batch_size=1000):
    """Populate ItemCredit rows from jsondata credit fields on all items.

    Restart-safe: skips items that already have credits. Shows last processed
    pk in the progress bar so you can resume with --start <pk>.
    """
    from django.db.models import Exists, OuterRef

    from catalog.models import Item, ItemCredit

    has_credits = ItemCredit.objects.filter(item_id=OuterRef("pk"))
    qs = (
        Item.objects.filter(
            is_deleted=False, merged_to_item__isnull=True, pk__gte=start_pk
        )
        .exclude(Exists(has_credits))
        .order_by("pk")
    )
    total = qs.count()
    logger.info(f"Items to process: {total} (starting after pk {start_pk})")
    created = 0
    last_pk = start_pk
    pending: list[ItemCredit] = []

    with tqdm(total=total, desc="Populating credits") as pbar:
        for item in qs.iterator():
            for field_name, credit_role in item.CREDIT_FIELD_MAPPING.items():
                values = getattr(item, field_name, None)
                if not values:
                    continue
                if isinstance(values, str):
                    values = [values]
                for i, value in enumerate(values):
                    if isinstance(value, dict):
                        name = value.get("name", "")
                        character = value.get("role") or ""
                    else:
                        name = str(value)
                        character = ""
                    if not name:
                        continue
                    pending.append(
                        ItemCredit(
                            item=item,
                            role=credit_role,
                            name=name,
                            character_name=character,
                            order=i,
                        )
                    )
            last_pk = item.pk
            pbar.update(1)
            pbar.set_postfix(pk=last_pk, created=created + len(pending))
            if len(pending) >= batch_size:
                ItemCredit.objects.bulk_create(pending)
                created += len(pending)
                pending = []

    if pending:
        ItemCredit.objects.bulk_create(pending)
        created += len(pending)

    logger.success(f"Credits: {created} created, last pk: {last_pk}")


def populate_credits_extra_20260415(batch_size=1000):
    """Populate ItemCredit rows for models that were missing CREDIT_FIELD_MAPPING.

    Covers TVShow, TVSeason, Podcast (new mappings) and Game artist (was missing).
    Unlike populate_credits_20260412 which skips items with any existing credits,
    this checks per-role so it won't duplicate credits that already exist.
    """
    from django.core.paginator import Paginator

    from catalog.models import (
        Edition,
        Game,
        Item,
        ItemCredit,
        PerformanceProduction,
        Podcast,
        TVSeason,
        TVShow,
    )

    new_mappings: list[tuple[type[Item], dict[str, str]]] = [
        (
            TVShow,
            {"director": "director", "playwright": "playwright", "actor": "actor"},
        ),
        (
            TVSeason,
            {"director": "director", "playwright": "playwright", "actor": "actor"},
        ),
        (Podcast, {"host": "host"}),
        (Game, {"artist": "artist"}),
        (Edition, {"imprint": "imprint"}),
        (
            PerformanceProduction,
            {
                "director": "director",
                "playwright": "playwright",
                "orig_creator": "original_creator",
                "composer": "composer",
                "choreographer": "choreographer",
                "actor": "actor",
                "performer": "performer",
                "crew": "crew",
                "troupe": "troupe",
            },
        ),
    ]
    total_created = 0
    for model_cls, field_mapping in new_mappings:
        qs = model_cls.objects.filter(
            is_deleted=False, merged_to_item__isnull=True
        ).order_by("pk")
        total = qs.count()
        logger.info(f"Processing {model_cls.__name__}: {total} items...")
        created = 0
        pending: list[ItemCredit] = []
        pg = Paginator(qs, batch_size)

        for page_num in tqdm(pg.page_range, desc=model_cls.__name__):
            for item in pg.get_page(page_num).object_list:
                existing_roles = {c.role for c in item.credits.all()}
                for field_name, credit_role in field_mapping.items():
                    if credit_role in existing_roles:
                        continue
                    values = getattr(item, field_name, None)
                    if not values:
                        continue
                    if isinstance(values, str):
                        values = [values]
                    for i, value in enumerate(values):
                        if isinstance(value, dict):
                            name = value.get("name", "")
                            character = value.get("role") or ""
                        else:
                            name = str(value)
                            character = ""
                        if not name:
                            continue
                        pending.append(
                            ItemCredit(
                                item=item,
                                role=credit_role,
                                name=name,
                                character_name=character,
                                order=i,
                            )
                        )
            if len(pending) >= batch_size:
                ItemCredit.objects.bulk_create(pending)
                created += len(pending)
                pending = []

        if pending:
            ItemCredit.objects.bulk_create(pending)
            created += len(pending)

        logger.info(f"{model_cls.__name__}: {created} credits created")
        total_created += created

    logger.success(f"Total credits created: {total_created}")


def link_credits_20260412():
    """Link unlinked ItemCredits to matching People items."""
    from catalog.models import ItemCredit, People

    people_by_name: dict[str, list] = {}
    for p in People.objects.filter(
        is_deleted=False, merged_to_item__isnull=True
    ).iterator():
        for entry in p.localized_name or []:
            name = entry.get("text", "")
            if name:
                people_by_name.setdefault(name, []).append(p)

    unlinked = ItemCredit.objects.filter(person__isnull=True)
    total = unlinked.count()
    linked = 0
    ambiguous = 0
    for credit in tqdm(unlinked.iterator(), total=total, desc="Linking credits"):
        matches = people_by_name.get(credit.name, [])
        if len(matches) == 1:
            credit.person = matches[0]
            credit.save(update_fields=["person"])
            linked += 1
        elif len(matches) > 1:
            ambiguous += 1
    logger.success(f"Linked: {linked}, ambiguous: {ambiguous}, total: {total}")


def reindex_people_20260417():
    """Initialize the people Typesense collection, purge any legacy People docs
    from the catalog collection, and populate the people collection from the DB.

    Safe to run repeatedly. Before this migration existed, People were indexed
    alongside every other Item in the `catalog` collection but with an empty
    `title` (People use `localized_name`), so they could not be found there.
    """
    from django.core.paginator import Paginator
    from django.db.models import Count

    from catalog.models import People
    from catalog.search import CatalogIndex, PeopleIndex

    catalog_index = CatalogIndex.instance()
    if not catalog_index.initialize_collection(max_wait=30):
        logger.error("Catalog index is not ready, people reindex aborted.")
        return
    people_index = PeopleIndex.instance()
    if not people_index.initialize_collection(max_wait=30):
        logger.error("People index is not ready, people reindex aborted.")
        return

    purged = catalog_index.delete_docs("item_class", "People")
    if purged:
        logger.warning(f"Purged {purged} legacy People docs from catalog index.")

    people_qs = (
        People.objects.filter(is_deleted=False, merged_to_item_id__isnull=True)
        .annotate(credit_count=Count("credited_items"))
        .prefetch_related("external_resources")
        .order_by("id")
    )
    pg = Paginator(people_qs, 1000)
    indexed = 0
    seen = 0
    for p in tqdm(pg.page_range, desc="People reindex"):
        docs = people_index.people_to_docs(pg.get_page(p).object_list)
        indexed += people_index.replace_docs(docs)
        seen += len(docs)
    logger.success(f"People reindex complete: {indexed} of {seen} docs indexed.")
