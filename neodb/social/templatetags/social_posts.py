from django import template

from takahe.models import Post

register = template.Library()

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
