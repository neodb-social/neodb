from django.http import HttpRequest

from api import schemas
from api.decorators import scope_required
from hatchway import api_view


@scope_required("read:statuses")
@api_view.get
def markers(request: HttpRequest) -> dict[str, schemas.Marker]:
    timelines = request.PARAMS.get("timeline[]", [])
    if not isinstance(timelines, list):
        timelines = [timelines]
    data = {}
    for m in request.identity.markers.filter(timeline__in=timelines):
        data[m.timeline] = schemas.Marker.from_marker(m)
    return data


@scope_required("write:statuses")
@api_view.post
def set_markers(request: HttpRequest) -> dict[str, schemas.Marker]:
    markers = {}
    # Build a flat {timeline: last_read_id} dict from either format:
    # - nested JSON: {"notifications": {"last_read_id": "123"}}
    # - bracket form params: notifications[last_read_id]=123
    timeline_ids: dict[str, str] = {}
    for key, value in request.PARAMS.items():
        if isinstance(value, dict) and "last_read_id" in value:
            timeline_ids[key] = value["last_read_id"]
        elif isinstance(value, str) and key.endswith("[last_read_id]"):
            timeline_ids[key.replace("[last_read_id]", "")] = value
    for timeline, last_id in timeline_ids.items():
        marker, created = request.identity.markers.get_or_create(
            timeline=timeline,
            defaults={
                "last_read_id": last_id,
            },
        )
        if not created:
            marker.last_read_id = last_id
            marker.save()
        markers[timeline] = schemas.Marker.from_marker(marker)
    return markers
