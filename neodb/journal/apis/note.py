from datetime import datetime
from typing import Any

from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.models import ItemSchema
from common.api import (
    NOT_FOUND,
    OK,
    PageNumberPagination,
    RedirectedResult,
    Result,
    api,
    resolve_item_for_read,
    resolve_item_for_write,
)
from common.sentry import record_activity
from takahe.utils import Takahe
from users.apis import UserIdentitySchema

from ..models import Note


class NoteSchema(Schema):
    uuid: str
    # No url/id here: a note has no page of its own, so it keeps the `Piece`
    # default url_path and Piece.url/absolute_url point at nothing.
    api_url: str
    post_id: int | None = Field(alias="latest_post_id")
    item: ItemSchema
    owner: UserIdentitySchema
    title: str | None
    content: str
    sensitive: bool = False
    progress_type: Note.ProgressType | None = None
    progress_value: str | None = None
    visibility: int = Field(ge=0, le=2)
    created_time: datetime

    @staticmethod
    def resolve_api_url(obj: Note) -> str:
        # the owner-scoped route is the one that resolves (PUT / DELETE)
        return f"/api/me/note/{obj.uuid}"


class NoteInSchema(Schema):
    title: str
    content: str
    sensitive: bool = False
    progress_type: Note.ProgressType | None = None
    progress_value: str | None = None
    visibility: int = Field(ge=0, le=2)
    post_to_fediverse: bool = False


class NotePageNumberPagination(PageNumberPagination):
    """Pagination that batch-loads takahe identities after slicing.

    ``select_related("owner")`` hands each row its own ``APIdentity``, so
    ``NoteSchema.owner`` would resolve display_name/avatar with one cross-db
    lookup per row.
    """

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: PageNumberPagination.Input,
        request: HttpRequest,
        **params: Any,
    ):
        val = super().paginate_queryset(queryset, pagination, request, **params)
        data = val.get("data")
        if data:
            Takahe.prefetch_takahe_identities([n.owner for n in data])
        return val


@api.get(
    "/me/note/item/{item_uuid}/",
    response={
        200: list[NoteSchema],
        302: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["note"],
)
@paginate(NotePageNumberPagination)
def list_notes_for_item(request, item_uuid: str, response: HttpResponse):
    """
    List notes by current user for an item

    If the item was merged into another one, HTTP 302 is returned.
    """
    item, redirect = resolve_item_for_read(
        item_uuid, "/api/me/note/item/{uuid}/", response
    )
    if not item:
        return redirect
    queryset = Note.objects.filter(
        owner=request.user.identity, item=item
    ).select_related("owner")
    return queryset.prefetch_related("item")


@api.post(
    "/me/note/item/{item_uuid}/",
    response={
        200: NoteSchema,
        307: RedirectedResult,
        401: Result,
        403: Result,
        404: Result,
    },
    tags=["note"],
)
def add_note_for_item(
    request, item_uuid: str, n_in: NoteInSchema, response: HttpResponse
):
    """
    Add a note for an item

    If the item was merged into another one, HTTP 307 is returned; repeat the
    request against the returned url.
    """
    item, redirect = resolve_item_for_write(
        item_uuid, "/api/me/note/item/{uuid}/", response
    )
    if not item:
        return redirect
    note = Note()
    note.item = item
    note.owner = request.user.identity
    note.title = n_in.title
    note.content = n_in.content
    note.sensitive = n_in.sensitive
    note.progress_type = n_in.progress_type
    note.progress_value = n_in.progress_value
    note.visibility = n_in.visibility
    note.crosspost_when_save = n_in.post_to_fediverse
    note.application_id_when_save = getattr(request, "application_id", None)
    note.save()
    record_activity("note", "api")
    return note


@api.put(
    "/me/note/{note_uuid}",
    response={200: NoteSchema, 401: Result, 403: Result, 404: Result},
    tags=["note"],
)
def update_note(request, note_uuid: str, n_in: NoteInSchema):
    """
    Update a note.
    """
    note = Note.get_by_url_and_owner(note_uuid, request.user.identity.pk)
    if not note:
        return NOT_FOUND
    note.title = n_in.title
    note.content = n_in.content
    note.sensitive = n_in.sensitive
    note.progress_type = n_in.progress_type
    note.progress_value = n_in.progress_value
    note.visibility = n_in.visibility
    note.crosspost_when_save = n_in.post_to_fediverse
    note.application_id_when_save = getattr(request, "application_id", None)
    note.save()
    record_activity("note", "api")
    return note


@api.delete(
    "/me/note/{note_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["note"],
)
def delete_note(request, note_uuid: str):
    """
    Delete a note.
    """
    note = Note.get_by_url_and_owner(note_uuid, request.user.identity.pk)
    if not note:
        return NOT_FOUND
    note.delete()
    return OK
