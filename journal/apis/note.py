from datetime import datetime
from typing import List

from ninja import Field, Schema
from ninja.pagination import paginate

from catalog.common.models import Item, ItemSchema
from common.api import NOT_FOUND, OK, PageNumberPagination, Result, api

from ..models import Note


class NoteSchema(Schema):
    uuid: str
    item: ItemSchema
    title: str
    content: str
    sensitive: bool = False
    progress_type: Note.ProgressType | None = None
    progress_value: str | None = None
    visibility: int = Field(ge=0, le=2)
    created_time: datetime


class NoteInSchema(Schema):
    title: str
    content: str
    sensitive: bool = False
    progress_type: Note.ProgressType | None = None
    progress_value: str | None = None
    visibility: int = Field(ge=0, le=2)
    post_to_fediverse: bool = False


@api.get(
    "/me/note/item/{item_uuid}/",
    response={200: List[NoteSchema], 401: Result, 403: Result},
    tags=["note"],
)
@paginate(PageNumberPagination)
def list_notes_for_item(request, item_uuid):
    """
    List notes by current user for an item
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    queryset = Note.objects.filter(owner=request.user.identity, item=item)
    return queryset.prefetch_related("item")


@api.post(
    "/me/note/item/{item_uuid}/",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["note"],
)
def add_note_for_item(request, item_uuid: str, n_in: NoteInSchema):
    """
    Add a note for an item
    """
    item = Item.get_by_url(item_uuid)
    if not item:
        return 404, {"message": "Item not found"}
    note = Note()
    note.title = n_in.title
    note.content = n_in.content
    note.sensitive = n_in.sensitive
    note.progress_type = n_in.progress_type
    note.progress_value = n_in.progress_value
    note.visibility = n_in.visibility
    note.crosspost_when_save = n_in.post_to_fediverse
    note.save()
    return 200, {"message": "OK"}


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
    note.save()
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
