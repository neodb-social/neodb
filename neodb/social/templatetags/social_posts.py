import re

from django import template
from django.utils.safestring import SafeString, mark_safe

from takahe.models import Post

register = template.Library()

# the "—<br><a>item</a> progress" paragraph Note.to_post_params appends
_NOTE_FOOTER = re.compile(r"<p>—<br\s*/?>\s*<a\s[^>]*>(?:(?!</p>).)*</p>\s*$", re.S)

PUBLIC_VISIBILITIES = (
    Post.Visibilities.public,
    Post.Visibilities.unlisted,
    Post.Visibilities.local_only,
)


@register.filter
def quoted_post_for(post: Post, user) -> Post | None:
    """The quoted post when the viewer may see it, else None so the template
    falls back to the bare quote link. A remote quote names any URI, so the
    stored post gets the same visibility check as a post of its own."""
    quoted = post.quoted_post_
    if not quoted:
        return None
    if quoted.visibility in PUBLIC_VISIBILITIES:
        # same as before this check existed; the query is only for the
        # followers-only and direct posts it protects
        return quoted
    viewer = user.identity.takahe_identity if user and user.is_authenticated else None
    visible = (
        Post.objects.not_hidden()
        .visible_to(viewer, include_replies=True)
        .filter(pk=quoted.pk)
        .exists()
    )
    return quoted if visible else None


@register.filter
def note_content_parts(post: Post) -> tuple[SafeString, SafeString]:
    """The body of a note post and its item footer, apart, so a collapsed body
    does not clip the link to the item."""
    html = post.safe_content_local
    m = _NOTE_FOOTER.search(html)
    if not m:
        return mark_safe(html), mark_safe("")
    return mark_safe(html[: m.start()]), mark_safe(m.group(0))
