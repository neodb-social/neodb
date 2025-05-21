from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models.lang import LOCALE_CHOICES, translate
from common.utils import AuthedHttpRequest, get_uuid_or_404
from journal.models.renderers import bleach_post_content
from takahe.models import Post
from takahe.utils import Takahe
from users.models import APIdentity

from ..forms import *
from ..models import *


def _can_view_post(post: Post, owner: APIdentity, viewer: APIdentity | None) -> int:
    if owner.deleted:
        return -2
    if owner.restricted and owner != viewer:
        return -2
    if not viewer:
        if post.visibility in [0, 1, 4]:
            if not post.local:
                return 0
            if not owner.anonymous_viewable:
                return -1
            return 1
        else:
            return -1
    if owner == viewer:
        return 1
    if viewer.is_blocking(owner) or owner.is_blocking(viewer):
        return -1
    if post.visibility in [0, 1, 4]:
        return 1
    if post.mentions.filter(pk=viewer.pk).exists():
        return 1
    if post.visibility == 2 and viewer.is_following(owner):
        return 1
    return -1


@login_required
def piece_replies(request: AuthedHttpRequest, piece_uuid: str):
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    replies = piece.get_replies(request.user.identity)
    reply_prepend = ""
    if piece.latest_post:
        reply_prepend = piece.latest_post.reply_prepend(
            request.user.identity.takahe_identity
        )
    return render(
        request,
        "replies.html",
        {"post": piece.latest_post, "replies": replies, "reply_prepend": reply_prepend},
    )


def post_replies(request: AuthedHttpRequest, post_id: int):
    post: Post = get_object_or_404(Post, pk=post_id)
    if post.state in ["deleted", "deleted_fanned_out"]:
        raise Http404("Post not available")
    viewer = request.user.identity if request.user.is_authenticated else None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner or _can_view_post(post, owner, viewer) < 0:
        raise PermissionDenied(_("Insufficient permission"))
    replies = Takahe.get_replies_for_posts([post_id], viewer.pk if viewer else None)
    reply_prepend = post.reply_prepend(request.user.identity.takahe_identity)
    return render(
        request,
        "replies.html",
        {
            "post": Takahe.get_post(post_id),
            "replies": replies,
            "reply_prepend": reply_prepend,
        },
    )


@require_http_methods(["POST"])
@login_required
def post_delete(request: AuthedHttpRequest, post_id: int):
    p = Takahe.get_post(post_id)
    if not p:
        raise Http404(_("Post not found"))
    if p.author_id != request.user.identity.pk:
        raise PermissionDenied(_("Insufficient permission"))
    Takahe.delete_posts([post_id])
    return HttpResponse("<!-- DELETED -->")


@require_http_methods(["POST"])
@login_required
def post_reply(request: AuthedHttpRequest, post_id: int):
    content = request.POST.get("content", "").strip()
    visibility = Takahe.Visibilities(int(request.POST.get("visibility", -1)))
    if not content:
        raise BadRequest(_("Invalid parameter"))
    Takahe.reply_post(post_id, request.user.identity.pk, content, visibility)
    replies = Takahe.get_replies_for_posts([post_id], request.user.identity.pk)
    post = Takahe.get_post(post_id)
    reply_prepend = ""
    if post:
        reply_prepend = post.reply_prepend(request.user.identity.takahe_identity)
    return render(
        request,
        "replies.html",
        {"post": post, "replies": replies, "reply_prepend": reply_prepend},
    )


@require_http_methods(["POST"])
@login_required
def post_boost(request: AuthedHttpRequest, post_id: int):
    # classic_crosspost = request.user.preference.mastodon_repost_mode == 1
    post = Takahe.get_post(post_id)
    if not post:
        raise BadRequest(_("Invalid parameter"))
    boost = Takahe.boost_post(post_id, request.user.identity.pk)
    if boost and boost.state == "new":
        if request.user.mastodon and request.user.preference.mastodon_repost_mode == 1:
            request.user.mastodon.boost_later(post.object_uri)
    return render(request, "action_boost_post.html", {"post": post})


@require_http_methods(["POST"])
@login_required
def post_pin(request: AuthedHttpRequest, post_id: int):
    post = Takahe.get_post(post_id)
    if not post or post.author_id != request.user.identity.pk:
        raise BadRequest(_("Invalid parameter"))
    Takahe.pin_post(post_id, request.user.identity.pk)
    return render(request, "action_pin_post.html", {"post": post})


@require_http_methods(["POST"])
@login_required
def post_like(request: AuthedHttpRequest, post_id: int):
    Takahe.like_post(post_id, request.user.identity.pk)
    return render(request, "action_like_post.html", {"post": Takahe.get_post(post_id)})


@require_http_methods(["POST"])
@login_required
def post_unlike(request: AuthedHttpRequest, post_id: int):
    Takahe.unlike_post(post_id, request.user.identity.pk)
    return render(request, "action_like_post.html", {"post": Takahe.get_post(post_id)})


@require_http_methods(["POST"])
@login_required
def post_translate(request, post_id: int):
    post: Post = get_object_or_404(Post, pk=post_id)
    if post.state in ["deleted", "deleted_fanned_out"]:
        raise Http404("Post not available")
    viewer = request.user.identity if request.user.is_authenticated else None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner or _can_view_post(post, owner, viewer) != 1:
        raise PermissionDenied(_("Insufficient permission"))
    text = bleach_post_content(post.content)
    text = translate(text, request.user.language, post.language)
    return HttpResponse(text)


@require_http_methods(["POST"])
@login_required
def post_flag(request, post_id: int):
    post: Post = get_object_or_404(Post, pk=post_id)
    if post.state in ["deleted", "deleted_fanned_out"]:
        raise Http404("Post not available")
    viewer = request.user.identity if request.user.is_authenticated else None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner or _can_view_post(post, owner, viewer) != 1:
        raise PermissionDenied(_("Insufficient permission"))
    reason = request.headers.get("HX-Prompt", "").strip()
    Takahe.report_post(post, request.user.identity.pk, reason)
    return HttpResponse("<script>alert('Report received.')</script>")


@login_required
@require_http_methods(["GET", "POST"])
def post_compose(request: AuthedHttpRequest):
    """
    Show the post compose form and handle form submission.
    """
    if request.method == "GET":
        return render(
            request,
            "post_compose.html",
            {
                "visibility": request.user.preference.default_visibility,
                "languages": LOCALE_CHOICES,
                "user_language": request.user.language,
                "image_count": 0,
            },
        )

    content = request.POST.get("content", "").strip()
    subject = request.POST.get("subject", "").strip()
    language = request.POST.get("language", request.user.language)
    visibility = Takahe.visibility_n2t(
        int(request.POST.get("visibility", 0)), request.user.preference.post_public_mode
    )

    if not content:
        raise BadRequest(_("Content cannot be empty."))
    if language == "x":
        language = ""

    attachments = []
    for i in range(4):  # Maximum 4 images
        image_file = request.FILES.get(f"image_{i}")
        if image_file:
            alt_text = request.POST.get(f"image_alt_{i}", "")
            if not image_file.name or not image_file.content_type:
                continue
            try:
                attachment = Takahe.upload_image(
                    request.user.identity.pk,
                    image_file.name,
                    image_file.read(),
                    image_file.content_type,
                    description=alt_text,
                )
                attachments.append(attachment)
            except Exception as e:
                logger.error(f"Failed to upload image: {e}")
                # Continue with the post even if image upload fails

    # Use subject as summary and set sensitive if subject is not empty
    Takahe.post(
        request.user.identity.pk,
        content,
        visibility,
        summary=subject if subject else None,
        sensitive=bool(subject),
        language=language,
        attachments=attachments if attachments else None,
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@require_http_methods(["GET"])
def post_view(request, handle: str, post_pk: int):
    if request.headers.get("HTTP_ACCEPT", "").endswith("json"):
        raise BadRequest("JSON not supported yet")
    post: Post = get_object_or_404(Post, pk=post_pk)
    if post.state in ["deleted", "deleted_fanned_out"]:
        raise Http404("Post not available")
    viewer = request.user.identity if request.user.is_authenticated else None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner:
        if not post.local:  # identity for remote post hasn't been sync to APIdentity
            return redirect(post.url)
        raise Http404("Post not available")
    h = handle.split("@", 2)
    username = h[0]
    if len(h) == 1:
        domain = settings.SITE_DOMAIN
    else:
        domain = h[1]
    if owner.username != username or owner.domain_name != domain:
        raise Http404("Post not available")
    match _can_view_post(post, owner, viewer):
        case 1:
            return render(request, "single_post.html", {"post": post, "owner": owner})
        case 0:
            if post.local:
                raise BadRequest("JSON not supported yet")
            else:
                return redirect(post.url)
        case -1:
            raise PermissionDenied()
        case _:
            raise Http404("Post not available")


@require_http_methods(["POST"])
@login_required
def post_vote(request: AuthedHttpRequest, post_id: int):
    choices = request.POST.getlist("choices")
    if not choices:
        raise BadRequest(_("Invalid choices"))
    post = Takahe.get_post(post_id)
    if not post or post.type != "Question":
        raise BadRequest(_("Invalid post"))
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner:
        raise Http404("Post not available")
    if not _can_view_post(post, owner, request.user.identity) > 0:
        raise PermissionDenied(_("Insufficient permission"))
    try:
        Takahe.vote_post(post, request.user.identity.pk, choices)
    except ValueError as e:
        raise BadRequest(str(e))
    return render(request, "post_question.html", {"post": post})
