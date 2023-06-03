import types
import logging
import typesense
from typesense.exceptions import ObjectNotFound
from django.conf import settings
from django.db.models.signals import post_save, post_delete
from catalog.models import Item
from pprint import pprint

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


def item_post_save_handler(sender, instance, created, **kwargs):
    if not created and settings.SEARCH_INDEX_NEW_ONLY:
        return
    Indexer.replace_item(instance)


def item_post_delete_handler(sender, instance, **kwargs):
    Indexer.delete_item(instance)


def tag_post_save_handler(sender, instance, **kwargs):
    pass


def tag_post_delete_handler(sender, instance, **kwargs):
    pass


class Indexer:
    class_map = {}
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = typesense.Client(settings.TYPESENSE_CONNECTION)
        return cls._instance

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
        idx = cls.instance().collections[INDEX_NAME]
        if idx:
            # idx.delete()
            idx.create(cls.config())

    @classmethod
    def delete_index(cls):
        idx = cls.instance().collections[INDEX_NAME]
        if idx:
            idx.delete()

    @classmethod
    def update_settings(cls):
        idx = cls.instance().collections[INDEX_NAME]
        if idx:
            idx.update(cls.config())

    @classmethod
    def get_stats(cls):
        idx = cls.instance().collections[INDEX_NAME]
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
        post_save.connect(item_post_save_handler, sender=model)
        post_delete.connect(item_post_delete_handler, sender=model)

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
            cls.instance().collections[INDEX_NAME].documents.upsert(
                cls.obj_to_dict(obj), {"dirty_values": "coerce_or_drop"}
            )
        except Exception as e:
            logger.error(f"replace item error: \n{e}")

    @classmethod
    def replace_batch(cls, objects):
        try:
            cls.instance().collections[INDEX_NAME].documents.import_(
                objects, {"action": "upsert"}
            )
        except Exception as e:
            logger.error(f"replace batch error: \n{e}")

    @classmethod
    def delete_item(cls, obj):
        pk = obj.uuid
        try:
            cls.instance().collections[INDEX_NAME].documents[pk].delete()
        except Exception as e:
            logger.error(f"delete item error: \n{e}")

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
            r = cls.instance().collections[INDEX_NAME].documents.search(options)
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
