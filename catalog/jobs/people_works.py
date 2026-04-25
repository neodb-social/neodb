import django_rq
from auditlog.context import set_actor

from users.models import User

from ..models import ExternalResource, IdType
from ..models.people import People
from ..search.utils import enqueue_fetch
from ..sites import SiteManager

FETCH_PEOPLE_WORKS_LOCK_TTL = 600


def _id_type_value(id_type) -> str:
    return id_type.value if hasattr(id_type, "value") else str(id_type)


def _site_supports_people_work_fetch(site_cls) -> bool:
    return bool(getattr(site_cls, "SUPPORTS_PEOPLE_WORK_FETCH", False))


def supported_people_work_source_ids() -> tuple[str, ...]:
    return tuple(
        _id_type_value(site_cls.ID_TYPE)
        for site_cls in SiteManager.registry.values()
        if site_cls.ID_TYPE and _site_supports_people_work_fetch(site_cls)
    )


def get_people_works_site_class(source_id_type):
    if not source_id_type:
        return None
    try:
        site_cls = SiteManager.get_site_cls_by_id_type(_id_type_value(source_id_type))
    except ValueError:
        return None
    return site_cls if _site_supports_people_work_fetch(site_cls) else None


def get_people_works_resource(
    person: People, resource_id: str | None = None
) -> ExternalResource | None:
    resources = person.external_resources.filter(
        id_type__in=supported_people_work_source_ids()
    )
    if resource_id:
        try:
            return resources.filter(pk=resource_id).first()
        except (TypeError, ValueError):
            return None
    return resources.first()


def get_people_works_source_label(source_id_type) -> str:
    site_cls = get_people_works_site_class(source_id_type)
    if not site_cls:
        return _id_type_value(source_id_type)
    return getattr(site_cls, "PEOPLE_WORKS_SOURCE_LABEL", None) or _id_type_value(
        source_id_type
    )


def enqueue_people_works(person: People, user: User, resource: ExternalResource):
    source_label = get_people_works_source_label(resource.id_type)
    person.log_action({"!fetch_works": [source_label, ""]})
    django_rq.get_queue("crawl").enqueue(
        fetch_works_for_person_task,
        person.uuid,
        user.pk,
        resource.id_type,
        resource.id_value,
    )


def fetch_works_for_person_task(
    person_uuid, user_id, source_id_type=None, source_id_value=None
):
    user = User.objects.filter(pk=user_id).first() if user_id else None
    with set_actor(user):
        person = People.get_by_url(person_uuid)
        if not person:
            return
        if (
            source_id_value is None
            and source_id_type
            and _id_type_value(source_id_type) not in supported_people_work_source_ids()
        ):
            # Backward compatibility for older queued jobs whose third argument
            # was the TMDB person id.
            source_id_value = source_id_type
            source_id_type = IdType.TMDB_Person
        if not source_id_type or not source_id_value:
            resource = get_people_works_resource(person)
            if not resource:
                return
            source_id_type = resource.id_type
            source_id_value = resource.id_value
        site_cls = get_people_works_site_class(source_id_type)
        if not site_cls:
            return
        site = site_cls(id_value=str(source_id_value))
        urls = site.fetch_people_work_urls()
        for url in urls:
            enqueue_fetch(url, is_refetch=False, user=user)
        person.link_matching_credits()
        source_label = get_people_works_source_label(source_id_type)
        person.log_action({"!fetch_works": [source_label, len(urls)]})
