from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.utils import AuthedHttpRequest, get_uuid_or_404, target_identity_required
from takahe.utils import Takahe

from ..forms import ArticleForm
from ..models import Article
from ..models.common import q_owned_piece_visible_to_user
from ..models.renderers import sanitize_md_images

_AP_ACCEPT_TYPES = (
    "application/activity+json",
    "application/ld+json",
)


def _wants_activitypub(request) -> bool:
    accept = request.headers.get("Accept", "")
    return any(t in accept for t in _AP_ACCEPT_TYPES)


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t and t.strip()]


@login_required
@require_http_methods(["GET", "POST"])
def article_edit(request: AuthedHttpRequest, article_uuid: str | None = None):
    article = None
    if article_uuid:
        article = get_object_or_404(Article, uid=get_uuid_or_404(article_uuid))
        if not article.is_editable_by(request.user):
            raise PermissionDenied(_("Insufficient permission"))
    if request.method == "GET":
        if article:
            initial = {
                "tags": ", ".join(article.normalized_tags),
                "share_to_mastodon": False,
            }
            form = ArticleForm(instance=article, initial=initial)
        else:
            form = ArticleForm(
                initial={
                    "share_to_mastodon": (
                        request.user.preference.mastodon_default_repost
                        if request.user.is_authenticated
                        else False
                    ),
                }
            )
        return render(
            request,
            "article_edit.html",
            {
                "form": form,
                "article": article,
            },
        )
    form = (
        ArticleForm(request.POST, instance=article)
        if article
        else ArticleForm(request.POST)
    )
    if not form.is_valid():
        raise BadRequest(_("Invalid parameter"))
    body = sanitize_md_images(form.cleaned_data["body"])
    tags = _parse_tags(form.cleaned_data.get("tags", ""))
    sensitive = bool(form.cleaned_data.get("sensitive", False))
    summary = form.cleaned_data.get("summary", "") if sensitive else ""
    article = Article.update_local_article(
        owner=request.user.identity,
        title=form.cleaned_data["title"],
        body=body,
        summary=summary,
        sensitive=sensitive,
        visibility=form.cleaned_data["visibility"],
        language=request.user.language or "",
        tags=tags,
        article=article,
        share_to_mastodon=bool(form.cleaned_data.get("share_to_mastodon", False)),
    )
    return redirect(reverse("journal:article_retrieve", args=[article.uuid]))


@require_http_methods(["GET", "HEAD"])
def article_retrieve(request, article_uuid: str):
    article = get_object_or_404(Article, uid=get_uuid_or_404(article_uuid))
    if request.method == "HEAD":
        return HttpResponse()
    if _wants_activitypub(request):
        # The Takahe Post is the canonical AP wire object — it owns
        # signing, caching, and visibility gating. Defer to it instead of
        # serving a duplicate AP envelope here. (For remote articles the
        # Takahe view in turn redirects to the origin's `object_uri`.)
        post = article.latest_post
        if not post:
            raise Http404("No post for article")
        return redirect(post.absolute_object_uri())
    if not article.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    return render(request, "article.html", {"article": article})


@target_identity_required
def user_article_list(request: AuthedHttpRequest, user_name):
    from journal.models.common import prefetch_latest_posts

    target = request.target_identity
    articles = list(
        Article.objects.filter(owner=target)
        .filter(q_owned_piece_visible_to_user(request.user, target))
        .order_by("-created_time")
    )
    prefetch_latest_posts(articles)
    if request.user.is_authenticated:
        posts = [a.latest_post for a in articles if a.latest_post]
        Takahe.prefetch_interaction_flags(posts, request.user.identity.pk)
    return render(
        request,
        "user_article_list.html",
        {
            "user": target.user,
            "identity": target,
            "articles": articles,
        },
    )
