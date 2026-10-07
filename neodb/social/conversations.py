import re
from collections.abc import Iterable

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import Truncator
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.models.misc import int_
from common.sentry import record_activity
from common.utils import AuthedHttpRequest
from takahe.models import Conversation, ConversationMembership, Identity, Post
from takahe.utils import Takahe
from users.models import APIdentity

PAGE_SIZE = 20
# Pixelfed drops a group message addressed to more people than this
GROUP_SIZE_HINT = 10

_MENTION = (
    r'(?:<span class="h-card"[^>]*>\s*<a\b[^>]*>.*?</a>\s*</span>'
    r"|<a\b[^>]*>\s*@.*?</a>)"
)
_LEADING_MENTIONS = re.compile(rf"^(\s*<p>)(?:\s*{_MENTION})+\s*", re.S)


def message_html(post: Post) -> SafeString:
    """
    The rendered post without the @mentions it opens with: in a conversation
    they only repeat who is in it.
    """
    html = post.safe_content_local
    stripped = _LEADING_MENTIONS.sub(r"\1", html, count=1)
    if strip_tags(stripped).strip() or len(post.attachments.all()):
        html = stripped
    # derived from sanitized output by removing whole elements only
    return mark_safe(html)  # nosec


def _api_identities(identities: Iterable[Identity]) -> dict[int, APIdentity]:
    identities = list(identities)
    found = {
        i.pk: i for i in APIdentity.objects.filter(pk__in=[t.pk for t in identities])
    }
    result = {}
    for t in identities:
        i = found.get(t.pk) or APIdentity.from_takahe(t)
        i.__dict__["takahe_identity"] = t
        result[t.pk] = i
    return result


class ConversationEntry:
    def __init__(
        self,
        conversation: Conversation,
        viewer_pk: int,
        identities: dict[int, APIdentity],
    ) -> None:
        self.pk = conversation.pk
        self.unread = bool(getattr(conversation, "viewer_unread", False))
        self.last_post = conversation.last_post
        participants = list(conversation.participants.all())
        others = [p for p in participants if p.pk != viewer_pk] or participants
        self.others = [identities[p.pk] for p in others]
        self.size = len(participants)

    @property
    def preview(self) -> str:
        if not self.last_post:
            return ""
        text = strip_tags(message_html(self.last_post)).strip()
        return Truncator(text).chars(120)


def _entries(conversations: list[Conversation], viewer_pk: int):
    identities = _api_identities(
        {p.pk: p for c in conversations for p in c.participants.all()}.values()
    )
    return [ConversationEntry(c, viewer_pk, identities) for c in conversations]


def _resolve_recipients(
    viewer: APIdentity, handles: str
) -> tuple[list[Identity], list[str]]:
    recipients: dict[int, Identity] = {}
    errors = []
    for raw in re.split(r"[\s,]+", handles):
        handle = raw.strip().lstrip("@")
        if not handle:
            continue
        if "@" in handle:
            username, _sep, domain = handle.partition("@")
            if not username or "." not in domain or "@" in domain:
                errors.append(_("@{handle} was not found.").format(handle=handle))
                continue
            identity = Takahe.get_identity_by_handler(username, domain)
            if not identity:
                Takahe.fetch_remote_identity(handle)
                errors.append(
                    _("@{handle} is not known here yet, try again in a moment.").format(
                        handle=handle
                    )
                )
                continue
        else:
            try:
                identity = APIdentity.get_by_handle(handle).takahe_identity
            except APIdentity.DoesNotExist:
                errors.append(_("@{handle} was not found.").format(handle=handle))
                continue
        target = APIdentity.from_takahe(identity)
        if target.pk == viewer.pk:
            continue
        if target.is_rejecting(viewer) or viewer.is_blocking(target):
            errors.append(
                _("You cannot send messages to @{handle}.").format(handle=handle)
            )
            continue
        recipients[identity.pk] = identity
    return list(recipients.values()), errors


def _membership_or_404(
    request: AuthedHttpRequest, conversation_id: int
) -> tuple[Conversation, ConversationMembership]:
    found = Takahe.get_conversation(request.user.identity.pk, conversation_id)
    if not found:
        raise Http404(_("Conversation not found"))
    return found


@login_required
@require_http_methods(["GET"])
def conversations(request: AuthedHttpRequest):
    viewer = request.user.identity
    qs = Takahe.get_conversations(viewer.pk)
    before = int_(request.GET.get("before"))
    if before:
        qs = qs.filter(last_post_id__lt=before)
    page = list(qs[: PAGE_SIZE + 1])
    entries = _entries(page[:PAGE_SIZE], viewer.pk)
    return render(
        request,
        "conversations.html",
        {
            "entries": entries,
            "next_before": entries[-1].last_post.pk if len(page) > PAGE_SIZE else None,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def conversation_new(request: AuthedHttpRequest):
    viewer = request.user.identity
    to = request.GET.get("to", "")
    errors: list[str] = []
    content = ""
    if request.method == "POST":
        to = request.POST.get("to", "")
        content = request.POST.get("content", "").strip()
        recipients, errors = _resolve_recipients(viewer, to)
        if not content:
            errors.append(_("Message cannot be empty."))
        if not recipients and not errors:
            errors.append(_("Add at least one recipient."))
        if not errors:
            post = Takahe.post(
                viewer.pk,
                content,
                Takahe.Visibilities.mentioned,
                mentions=recipients,
            )
            record_activity("post", "web")
            if post and post.conversation_id:
                return redirect("social:conversation", post.conversation_id)
            return redirect("social:conversations")
    return render(
        request,
        "conversation_new.html",
        {
            "to": to,
            "content": content,
            "errors": errors,
            "group_size_hint": GROUP_SIZE_HINT,
        },
    )


@login_required
@require_http_methods(["GET"])
def conversation(request: AuthedHttpRequest, conversation_id: int):
    viewer = request.user.identity
    conv, membership = _membership_or_404(request, conversation_id)
    before = int_(request.GET.get("before"))
    posts = Takahe.get_conversation_posts(conv.pk, before_pk=before or None)
    if not before:
        Takahe.set_conversation_unread(membership, False)
    has_earlier = (
        bool(posts)
        and Post.objects.not_hidden()
        .filter(conversation_id=conv.pk, id__lt=posts[0].pk)
        .exists()
    )
    authors = _api_identities({p.author_id: p.author for p in posts}.values())
    return render(
        request,
        "conversation.html",
        {
            "entry": _entries([conv], viewer.pk)[0],
            # not "messages", which the header renders as django.contrib.messages
            "chat_messages": [
                (p, authors[p.author_id], message_html(p)) for p in posts
            ],
            "earlier_before": posts[0].pk if has_earlier else None,
            "viewer_pk": viewer.pk,
            "group_size_hint": GROUP_SIZE_HINT,
        },
    )


@login_required
@require_http_methods(["POST"])
def conversation_reply(request: AuthedHttpRequest, conversation_id: int):
    conv, _membership = _membership_or_404(request, conversation_id)
    content = request.POST.get("content", "").strip()
    if not content:
        raise BadRequest(_("Message cannot be empty."))
    Takahe.post_in_conversation(request.user.identity.pk, conv, content)
    record_activity("post", "web")
    return redirect(reverse("social:conversation", args=[conv.pk]) + "#latest")


@login_required
@require_http_methods(["POST"])
def conversation_unread(request: AuthedHttpRequest, conversation_id: int):
    _conv, membership = _membership_or_404(request, conversation_id)
    Takahe.set_conversation_unread(membership, True)
    return redirect("social:conversations")


@login_required
@require_http_methods(["POST"])
def conversation_dismiss(request: AuthedHttpRequest, conversation_id: int):
    _conv, membership = _membership_or_404(request, conversation_id)
    Takahe.dismiss_conversation(membership)
    return redirect("social:conversations")
