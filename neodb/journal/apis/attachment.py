"""Upload API for user attachments -- the app-side counterpart of the web
markdown editor's image-upload flow.

An app uploads the file here, gets back a URL, and embeds that URL in the
markdown body it then sends to the article / review / collection APIs. The
piece link is established when that body is saved, so the order matters:
upload first, embed second.
"""

import uuid
from datetime import datetime
from typing import List

from django import forms
from ninja import File, Form, Schema, Status
from ninja.files import UploadedFile
from ninja.pagination import paginate

from common.api import NOT_FOUND, OK, PageNumberPagination, Result, api

from ..models import Attachment

# Matches the web upload endpoint (journal.views.common) so both entry points
# accept exactly the same files.
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB


class AttachmentSchema(Schema):
    uuid: str
    url: str
    preview_url: str
    mimetype: str
    size: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    description: str = ""
    created_time: datetime


@api.get(
    "/me/attachment/",
    response={200: List[AttachmentSchema], 401: Result, 403: Result},
    tags=["attachment"],
)
@paginate(PageNumberPagination)
def list_attachments(request):
    """
    List files uploaded by the current user, newest first.
    """
    return Attachment.objects.filter(owner=request.user.identity).order_by(
        "-created_time"
    )


@api.post(
    "/me/attachment/",
    response={200: AttachmentSchema, 400: Result, 401: Result, 403: Result},
    tags=["attachment"],
)
def upload_attachment(request, file: File[UploadedFile], description: Form[str] = ""):
    """
    Upload a file and register it as an attachment owned by the current user.

    Send it as `multipart/form-data` with a `file` field; it must be a valid
    image no larger than 10MB. The returned `url` is what you embed in the
    markdown body of an article, review or collection -- the attachment is
    linked to the piece when that body is saved.
    """
    if (file.size or 0) > MAX_ATTACHMENT_SIZE:
        return Status(400, {"message": "File too large"})
    # Validate by decoding rather than trusting the declared content type, and
    # take the extension from the detected format so the stored path is
    # meaningful even for an extension-less upload.
    try:
        f = forms.ImageField().to_python(file)
    except forms.ValidationError:
        return Status(400, {"message": "Invalid image file"})
    if f is None:
        return Status(400, {"message": "Invalid image file"})
    image = getattr(f, "image", None)  # set by ImageField.to_python
    fmt = (image.format or "").lower() if image else ""
    ext = {"jpeg": "jpg"}.get(fmt, fmt)
    mimetype = f"image/{'jpeg' if ext == 'jpg' else ext}"
    if not ext or mimetype not in ALLOWED_IMAGE_TYPES:
        return Status(400, {"message": "Unsupported image type"})
    width, height = image.size if image else (None, None)
    f.seek(0)
    return Attachment.register(
        request.user.identity,
        f,
        ext,
        mimetype=mimetype,
        description=description,
        width=width,
        height=height,
    )


@api.delete(
    "/me/attachment/{attachment_uuid}",
    response={200: Result, 401: Result, 403: Result, 404: Result},
    tags=["attachment"],
)
def delete_attachment(request, attachment_uuid: str):
    """
    Delete a file uploaded by the current user, and its stored bytes.

    Pieces still embedding it will render a broken image, so unlink it from
    them first if that matters.
    """
    try:
        uid = uuid.UUID(attachment_uuid)
    except ValueError:
        return NOT_FOUND
    attachment = Attachment.objects.filter(owner=request.user.identity, uid=uid).first()
    if not attachment:
        return NOT_FOUND
    # Attachment.delete reclaims the bytes itself
    attachment.delete()
    return OK
