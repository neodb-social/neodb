import uuid

from django.utils import timezone


def resource_cover_path(resource, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return "item/" + resource.id_type + "/" + fn


def item_cover_path(item, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return "item/" + item.category + "/" + fn


def piece_cover_path(item, filename):
    fn = (
        timezone.now().strftime("%Y/%m/%d/")
        + str(uuid.uuid4())
        + "."
        + filename.split(".")[-1]
    )
    return f"user/{item.owner_id or '_'}/{fn}"
