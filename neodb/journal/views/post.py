from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from loguru import logger

from common.models.lang import LOCALE_CHOICES, translate
from common.models.misc import int_
from common.sentry import record_activity
from common.utils import AuthedHttpRequest, get_uuid_or_404
from journal.models.renderers import bleach_post_content
from takahe.models import Post
from takahe.utils import Takahe
from users.models import APIdentity

from ..forms import *
from ..models import *
from .common import conditional_get_for_anonymous


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


def piece_replies(request: AuthedHttpRequest, piece_uuid: str):
    # Anonymous viewers can still load the replies panel (list only — the
    # compose form in ``replies.html`` is gated on ``request.user.is_authenticated``
    # via the included templates), mirroring ``post_quote``'s anonymous GET.
    piece = get_object_or_404(Piece, uid=get_uuid_or_404(piece_uuid))
    if not piece.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    viewer = request.user.identity if request.user.is_authenticated else None
    replies = piece.get_replies(viewer)
    reply_prepend = ""
    if piece.latest_post and viewer:
        reply_prepend = piece.latest_post.reply_prepend(viewer.takahe_identity)
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
    reply_prepend = post.reply_prepend(viewer.takahe_identity) if viewer else ""
    return render(
        request,
        "replies.html",
        {
            "post": Takahe.get_post(post_id),
            "replies": replies,
            "reply_prepend": reply_prepend,
            "show_header": request.GET.get("header") == "1",
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
    try:
        visibility = Takahe.Visibilities(int(request.POST.get("visibility", "")))
    except ValueError, KeyError:
        raise BadRequest(_("Invalid parameter"))
    if not content:
        raise BadRequest(_("Invalid parameter"))
    post = Takahe.get_post(post_id)
    if post:
        mentions_to_prepend = post.reply_prepend(request.user.identity.takahe_identity)
        if mentions_to_prepend and not content.startswith(mentions_to_prepend):
            content = mentions_to_prepend + content
    Takahe.reply_post(post_id, request.user.identity.pk, content, visibility)
    record_activity("post", "web")
    replies = Takahe.get_replies_for_posts([post_id], request.user.identity.pk)
    reply_prepend = ""
    if post:
        reply_prepend = post.reply_prepend(request.user.identity.takahe_identity)
    return render(
        request,
        "replies.html",
        {"post": post, "replies": replies, "reply_prepend": reply_prepend},
    )


def _allowed_quote_visibilities(
    quoted_visibility: int,
) -> list[int]:
    """
    Return allowed visibilities for a post quoting a post with given visibility.
    Visibility ordering: public(0) > unlisted(1) > local_only(4) > followers(2) > mentioned(3)
    Exception: local_only can only be quoted as local_only or mentioned.
    """
    V = Takahe.Visibilities
    if quoted_visibility == V.public:
        return [V.public, V.unlisted, V.local_only, V.followers, V.mentioned]
    elif quoted_visibility == V.unlisted:
        return [V.unlisted, V.local_only, V.followers, V.mentioned]
    elif quoted_visibility == V.local_only:
        return [V.local_only, V.mentioned]
    elif quoted_visibility == V.followers:
        return [V.followers, V.mentioned]
    else:  # mentioned
        return [V.mentioned]


@require_http_methods(["GET", "POST"])
def post_quote(request: AuthedHttpRequest, post_id: int):
    # Anonymous GET is allowed so the click-to-load panel renders the
    # existing-quotes list for logged-out viewers (mirrors how
    # ``post_replies`` exposes the replies panel anonymously).
    post = Takahe.get_post(post_id)
    if not post or post.state in ["deleted", "deleted_fanned_out"]:
        raise BadRequest(_("Invalid parameter"))
    viewer = request.user.identity if request.user.is_authenticated else None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner or _can_view_post(post, owner, viewer) < 0:
        raise PermissionDenied(_("Insufficient permission"))
    allowed: list = []
    default_visibility = 0
    submitted = False
    if request.user.is_authenticated:
        allowed = _allowed_quote_visibilities(post.visibility)
        public_mode = request.user.preference.post_public_mode
        default_visibility = public_mode if public_mode in allowed else allowed[0]
        if request.method == "POST":
            content = request.POST.get("content", "").strip()
            try:
                visibility = Takahe.Visibilities(
                    int(request.POST.get("visibility", -1))
                )
            except ValueError:
                raise BadRequest(_("Invalid parameter"))
            if not content:
                raise BadRequest(_("Invalid parameter"))
            if visibility not in allowed:
                raise BadRequest(_("Visibility too high for quoting this post"))
            Takahe.post(
                request.user.identity.pk,
                content,
                visibility,
                quote_url=post.object_uri,
            )
            record_activity("post", "web")
            submitted = True
    elif request.method == "POST":
        raise PermissionDenied(_("Insufficient permission"))
    viewer_takahe = viewer.takahe_identity if viewer else None
    quotes = (
        Post.objects.not_hidden()
        .visible_to(viewer_takahe, include_replies=True)
        .filter(quote_url=post.object_uri)
        .select_related("author")
        .prefetch_related("mentions")
        .order_by("-published")[:20]
    )
    return render(
        request,
        "post_quotes.html",
        {
            "post": post,
            "allowed_visibilities": allowed,
            "default_visibility": default_visibility,
            "quotes": quotes,
            "submitted": submitted,
        },
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
        if request.user.mastodon and request.user.preference.mastodon_boost_enabled:
            request.user.mastodon.boost_later(post.object_uri)
    return render(request, "action_boost_post.html", {"post": post})


@require_http_methods(["POST"])
@login_required
def post_pin(request: AuthedHttpRequest, post_id: int):
    post = Takahe.get_post(post_id)
    if not post or post.author_id != request.user.identity.pk:
        raise BadRequest(_("Invalid parameter"))
    Takahe.pin_post(post_id, request.user.identity.pk)
    menu_label = request.POST.get("menu_label") == "1"
    return render(
        request, "action_pin_post.html", {"post": post, "menu_label": menu_label}
    )


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
    reply_to = request.GET.get("reply_to", request.POST.get("reply_to", ""))
    reply_to_name = ""
    if reply_to:
        try:
            r = APIdentity.get_by_handle(reply_to)
            if r.is_rejecting(request.user.identity):
                raise APIdentity.DoesNotExist()
            reply_to = r.full_handle
            reply_to_name = r.display_name
        except APIdentity.DoesNotExist:
            reply_to = ""
    visibility = int_(request.GET.get("visibility", request.POST.get("visibility")), -1)
    if visibility not in [0, 1, 2]:
        visibility = request.user.preference.post_public_mode
    if request.method == "GET":
        return render(
            request,
            "post_compose.html",
            {
                "reply_to": reply_to,
                "reply_to_name": reply_to_name,
                "visibility": visibility,
                "languages": LOCALE_CHOICES,
                "user_language": request.user.language,
                "image_count": 0,
            },
        )

    content = request.POST.get("content", "").strip()
    sensitive = request.POST.get("sensitive") in ("1", "on", "true", "True")
    # Subject is the content warning; only meaningful when the user
    # explicitly marked the post sensitive.
    subject = request.POST.get("subject", "").strip() if sensitive else ""
    language = request.POST.get("language", request.user.language)
    visibility2 = Takahe.visibility_n2t(
        visibility, request.user.preference.post_public_mode
    )
    if not content:
        raise BadRequest(_("Content cannot be empty."))
    if reply_to and f"@{reply_to}" not in content.split():
        content = f"@{reply_to} {content}"
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

    Takahe.post(
        request.user.identity.pk,
        content,
        visibility2,
        summary=subject or None,
        sensitive=sensitive,
        language=language or "",
        attachments=attachments if attachments else None,
    )
    record_activity("post", "web")
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        referer = "/"
    return HttpResponseRedirect(referer)


@login_required
@require_http_methods(["GET", "POST"])
def post_edit(request: AuthedHttpRequest, post_id: int):
    """
    Show edit form for a free-form post (not linked to any item/piece) and
    handle the submission.
    """
    post = Takahe.get_post(post_id)
    if not post or post.state in ["deleted", "deleted_fanned_out"]:
        raise Http404(_("Post not found"))
    if post.author_id != request.user.identity.pk:
        raise PermissionDenied(_("Insufficient permission"))
    if post.piece is not None:
        raise PermissionDenied(_("This post cannot be edited here"))

    if request.method == "GET":
        return render(
            request,
            "post_compose.html",
            {
                "edit_post": post,
                "content": post.content_plain_text,
                "subject": post.summary or "",
                "sensitive": post.sensitive,
                "visibility": Takahe.visibility_t2n(post.visibility),
                "languages": LOCALE_CHOICES,
                "user_language": post.language or request.user.language,
            },
        )

    content = request.POST.get("content", "").strip()
    sensitive = request.POST.get("sensitive") in ("1", "on", "true", "True")
    subject = request.POST.get("subject", "").strip() if sensitive else ""
    language = request.POST.get("language", request.user.language)
    if not content:
        raise BadRequest(_("Content cannot be empty."))
    if language == "x":
        language = ""

    Takahe.post(
        request.user.identity.pk,
        content,
        post.visibility,
        summary=subject or None,
        sensitive=sensitive,
        language=language or "",
        post_pk=post.pk,
    )
    record_activity("post", "web")
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        referer = "/"
    return HttpResponseRedirect(referer)


def _post_last_modified(request, handle: str, post_pk: int):
    # Full visibility + handle gating runs here so a 304 can never bypass
    # checks the view would otherwise enforce: handle mismatch
    # (``/@wrong/posts/<id>/``), owner-level privacy toggles
    # (``anonymous_viewable``, ``restricted``, ``deleted``), and
    # post-level visibility. Each of those can change without bumping
    # ``Post.updated``. Anything not 100% safe to 304 returns ``None`` so
    # the view body produces the real 404/403/redirect.
    post = (
        Post.objects.filter(pk=post_pk)
        .exclude(state__in=["deleted", "deleted_fanned_out"])
        .select_related("author")
        .first()
    )
    if not post:
        return None
    owner = APIdentity.by_takahe_identity(post.author)
    if not owner:
        return None
    h = handle.split("@", 2)
    username = h[0]
    domain = h[1] if len(h) > 1 else settings.SITE_DOMAIN
    if owner.username != username or owner.domain_name != domain:
        return None
    if _can_view_post(post, owner, viewer=None) != 1:
        return None
    # Article/Review/Collection posts redirect to the canonical piece URL;
    # never let a 304 short-circuit that 302.
    piece = post.piece
    if piece is not None and piece.classname in ("article", "review", "collection"):
        return None
    return post.updated


@require_http_methods(["GET", "HEAD"])
@conditional_get_for_anonymous(_post_last_modified)
def post_view(request, handle: str, post_pk: int):
    if request.headers.get("Accept", "").endswith("json"):
        raise BadRequest("JSON not supported yet")
    post: Post = get_object_or_404(
        Post.objects.select_related("preview_card"),
        pk=post_pk,
    )
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
    if request.method == "HEAD":
        return HttpResponse()
    match _can_view_post(post, owner, viewer):
        case 1:
            # Article/Review/Collection posts have a richer canonical view
            # at ``/article/<uuid>`` / ``/review/<uuid>`` / ``/collection/<uuid>``;
            # redirect HTML browsers there instead of the bare single_post shell.
            piece = post.piece
            if piece is not None and piece.classname in (
                "article",
                "review",
                "collection",
            ):
                return redirect(piece.url)
            quotes_count = (
                Post.objects.filter(quote_url=post.object_uri)
                .exclude(state__in=["deleted", "deleted_fanned_out"])
                .count()
            )
            return render(
                request,
                "single_post.html",
                {"post": post, "owner": owner, "quotes_count": quotes_count},
            )
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
