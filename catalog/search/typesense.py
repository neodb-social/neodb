import types
import logging
import typesense
from typesense.exceptions import ObjectNotFound
from typesense.collection import Collection
from django.conf import settings
from django.db.models.signals import post_save, post_delete
from catalog.models import Item
from pprint import pprint
import django_rq
from rq.job import Job
from django_redis import get_redis_connection

INDEX_NAME = "catalog"
SEARCHABLE_ATTRIBUTES = [
    "title",
    "orig_title",
    "other_title",
    "subtitle",
    "artist",
    "author",
    "translator",
    "developer",
    "director",
    "actor",
    "playwright",
    "pub_house",
    "company",
    "publisher",
    "isbn",
    "imdb",
    "barcode",
]
FILTERABLE_ATTRIBUTES = ["category", "tags", "class_name"]
INDEXABLE_DIRECT_TYPES = [
    "BigAutoField",
    "BooleanField",
    "CharField",
    "PositiveIntegerField",
    "PositiveSmallIntegerField",
    "TextField",
    "ArrayField",
]
INDEXABLE_TIME_TYPES = ["DateTimeField"]
INDEXABLE_DICT_TYPES = ["JSONField"]
INDEXABLE_FLOAT_TYPES = ["DecimalField"]
SORTING_ATTRIBUTE = None
# NONINDEXABLE_TYPES = ['ForeignKey', 'FileField',]
SEARCH_PAGE_SIZE = 20


logger = logging.getLogger(__name__)


_PENDING_INDEX_KEY = "pending_index_ids"
_PENDING_INDEX_QUEUE = "import"
_PENDING_INDEX_JOB_ID = "pending_index_flush2"


def _update_index_task():
    item_ids = get_redis_connection("default").spop(_PENDING_INDEX_KEY, 1000)
    updated = 0
    while item_ids:
        items = Item.objects.filter(id__in=item_ids)
        Indexer.replace_batch(items)
        updated += len(items)
        item_ids = get_redis_connection("default").spop(_PENDING_INDEX_KEY, 1000)
    logger.info(f"Index updated for {updated} items")


def enqueue_update_index(item_ids):
    if not item_ids:
        return
    get_redis_connection("default").sadd(_PENDING_INDEX_KEY, *item_ids)
    queued = False
    try:
        job = Job.fetch(
            id=_PENDING_INDEX_JOB_ID,
            connection=django_rq.get_connection(_PENDING_INDEX_QUEUE),
        )
        queued = job.get_status() == "queued"
    except:
        queued = False
    if not queued:
        django_rq.get_queue(_PENDING_INDEX_QUEUE).enqueue(
            _update_index_task, job_id=_PENDING_INDEX_JOB_ID
        )


def _item_post_save_handler(sender, instance, created, **kwargs):
    if not created and settings.SEARCH_INDEX_NEW_ONLY:
        return
    Indexer.replace_item(instance)


def _item_post_delete_handler(sender, instance, **kwargs):
    Indexer.delete_item(instance)


def _list_post_save_handler(sender, instance, created, **kwargs):
    ids = instance.items.all().values_list("id", flat=True)
    enqueue_update_index(ids)


def _list_post_delete_handler(sender, instance, **kwargs):
    pass


def _piece_post_save_handler(sender, instance, created, **kwargs):
    enqueue_update_index([instance.item_id])


def _piece_post_delete_handler(sender, instance, **kwargs):
    enqueue_update_index([instance.item_id])


class Indexer:
    class_map = {}
    _instance = None

    @classmethod
    def instance(cls) -> Collection:
        if cls._instance is None:
            cls._instance = typesense.Client(settings.TYPESENSE_CONNECTION).collections[
                INDEX_NAME
            ]
        return cls._instance  # type: ignore

    @classmethod
    def config(cls):
        # fields = [
        #     {"name": "_class", "type": "string", "facet": True},
        #     {"name": "source_site", "type": "string", "facet": True},
        #     {"name": ".*", "type": "auto", "locale": "zh"},
        # ]
        # use dumb schema below before typesense fix a bug
        fields = [
            {"name": "id", "type": "string"},
            {"name": "category", "type": "string", "facet": True},
            {"name": "class_name", "type": "string", "facet": True},
            {"name": "rating_count", "optional": True, "type": "int32", "facet": True},
            {"name": "isbn", "optional": True, "type": "string"},
            {"name": "imdb", "optional": True, "type": "string"},
            {"name": "barcode", "optional": True, "type": "string"},
            {"name": "author", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": "orig_title", "optional": True, "locale": "zh", "type": "string"},
            {"name": "pub_house", "optional": True, "locale": "zh", "type": "string"},
            {"name": "title", "optional": True, "locale": "zh", "type": "string"},
            {
                "name": "translator",
                "optional": True,
                "locale": "zh",
                "type": "string[]",
            },
            {"name": "subtitle", "optional": True, "locale": "zh", "type": "string"},
            {"name": "artist", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": "company", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": "developer", "optional": True, "locale": "zh", "type": "string[]"},
            {
                "name": "other_title",
                "optional": True,
                "locale": "zh",
                "type": "string[]",
            },
            {"name": "publisher", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": "actor", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": "director", "optional": True, "locale": "zh", "type": "string[]"},
            {
                "name": "playwright",
                "optional": True,
                "locale": "zh",
                "type": "string[]",
            },
            {"name": "tags", "optional": True, "locale": "zh", "type": "string[]"},
            {"name": ".*", "optional": True, "locale": "zh", "type": "auto"},
        ]
        return {
            "name": INDEX_NAME,
            "fields": fields,
            # "default_sorting_field": "rating_count",
        }

    @classmethod
    def init(cls):
        idx = typesense.Client(settings.TYPESENSE_CONNECTION).collections
        if idx:
            # idx.delete()
            idx.create(cls.config())

    @classmethod
    def delete_index(cls):
        idx = cls.instance()
        if idx:
            idx.delete()

    @classmethod
    def update_settings(cls):
        idx = cls.instance()
        if idx:
            idx.update(cls.config())

    @classmethod
    def get_stats(cls):
        idx = cls.instance()
        if idx:
            return idx.retrieve()

    @classmethod
    def busy(cls):
        return False

    @classmethod
    def update_model_indexable(cls, model):
        cls.class_map[model.__name__.lower()] = model
        model.indexable_fields = ["tags"]
        model.indexable_fields_time = []
        model.indexable_fields_dict = []
        model.indexable_fields_float = []
        for field in model._meta.get_fields():
            type = field.get_internal_type()
            if type in INDEXABLE_DIRECT_TYPES:
                model.indexable_fields.append(field.name)
            elif type in INDEXABLE_TIME_TYPES:
                model.indexable_fields_time.append(field.name)
            elif type in INDEXABLE_DICT_TYPES:
                model.indexable_fields_dict.append(field.name)
            elif type in INDEXABLE_FLOAT_TYPES:
                model.indexable_fields_float.append(field.name)
        i = model()
        for f in ["imdb", "isbn", "barcode"]:  # FIXME
            if hasattr(i, f):
                model.indexable_fields.append(f)
        post_save.connect(_item_post_save_handler, sender=model)
        post_delete.connect(_item_post_delete_handler, sender=model)

    @classmethod
    def register_list_model(cls, list_model):
        post_save.connect(_list_post_save_handler, sender=list_model)
        # post_delete.connect(_list_post_delete_handler, sender=list_model)  # covered in list_model delete signal
        post_save.connect(_piece_post_save_handler, sender=list_model.MEMBER_CLASS)
        post_delete.connect(_piece_post_delete_handler, sender=list_model.MEMBER_CLASS)

    @classmethod
    def register_piece_model(cls, model):
        post_save.connect(_piece_post_save_handler, sender=model)
        post_delete.connect(_piece_post_delete_handler, sender=model)

    @classmethod
    def obj_to_dict(cls, obj):
        item = {}
        for field in obj.__class__.indexable_fields:
            item[field] = getattr(obj, field)
        for field in obj.__class__.indexable_fields_time:
            item[field] = (
                getattr(obj, field).timestamp() if getattr(obj, field) else None
            )
        for field in obj.__class__.indexable_fields_float:
            item[field] = float(getattr(obj, field)) if getattr(obj, field) else None
        for field in obj.__class__.indexable_fields_dict:
            d = getattr(obj, field)
            if d.__class__ is dict:
                item.update(d)

        item["id"] = obj.uuid
        item["category"] = obj.category.value
        item["class_name"] = obj.class_name
        item = {
            k: v
            for k, v in item.items()
            if v
            and (k in SEARCHABLE_ATTRIBUTES or k in FILTERABLE_ATTRIBUTES or k == "id")
        }
        # typesense requires primary key to be named 'id', type string
        item["rating_count"] = obj.rating_count

        return item

    @classmethod
    def replace_item(cls, obj):
        if obj.is_deleted or obj.merged_to_item_id:
            return cls.delete_item(obj)
        try:
            cls.instance().documents.upsert(
                cls.obj_to_dict(obj), {"dirty_values": "coerce_or_drop"}
            )
        except Exception as e:
            logger.error(f"replace item error: \n{e}")

    @classmethod
    def replace_batch(cls, objects):
        try:
            items = list(
                map(
                    lambda o: cls.obj_to_dict(o),
                    [x for x in objects if hasattr(x, "indexable_fields")],
                )
            )
            # TODO check is_deleted=False, merged_to_item_id__isnull=True and call delete_batch()
            if items:
                cls.instance().documents.import_(items, {"action": "upsert"})
        except Exception as e:
            logger.error(f"replace batch error: \n{e}")

    @classmethod
    def delete_item(cls, obj):
        pk = obj.uuid
        try:
            cls.instance().documents[pk].delete()
        except Exception as e:
            logger.warn(f"delete item error: \n{e}")

    @classmethod
    def search(cls, q, page=1, category=None, tag=None, sort=None):
        f = []
        if category:
            if category == "movietv":
                f.append("category:= [movie,tv]")
            else:
                f.append("category:= " + category)
        if tag:
            f.append(f"tags:= '{tag}'")
        filters = " && ".join(f)
        options = {
            "q": q,
            "page": page,
            "per_page": SEARCH_PAGE_SIZE,
            "query_by": ",".join(SEARCHABLE_ATTRIBUTES),
            "filter_by": filters,
            # "facet_by": "category",
            "sort_by": "_text_match:desc,rating_count:desc"
            # 'facetsDistribution': ['_class'],
            # 'sort_by': None,
        }
        results = types.SimpleNamespace()

        try:
            r = cls.instance().documents.search(options)
            # pprint(r)
            results.items = list(
                [
                    x
                    for x in map(lambda i: cls.item_to_obj(i["document"]), r["hits"])
                    if x is not None
                ]
            )
            results.count = r["found"]
            results.num_pages = (r["found"] + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE
        except ObjectNotFound:
            results.items = []
            results.count = 0
            results.num_pages = 1

        return results

    @classmethod
    def item_to_obj(cls, item):
        try:
            return Item.get_by_url(item["id"])
        except Exception as e:
            logger.error(f"unable to load search result item from db:{item}\n{e}")
            return None
