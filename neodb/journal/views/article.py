from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.syndication.views import Feed
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.sentry import record_activity
from common.utils import AuthedHttpRequest, get_uuid_or_404, target_identity_required
from takahe.utils import Takahe
from users.middlewares import activate_language_for_user
from users.models.apidentity import APIdentity

from ..forms import ArticleForm
from ..models import Article
from ..models.common import prefetch_latest_posts, q_owned_piece_visible_to_user
from ..models.renderers import convert_leading_space_in_md
from .common import conditional_get_for_anonymous, post_quotes_count


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
        # Re-render with the bound form so users see field-level errors
        # instead of a generic 400 page.
        return render(
            request,
            "article_edit.html",
            {"form": form, "article": article},
            status=400,
        )
    body = form.cleaned_data["body"]
    if form.cleaned_data.get("leading_space"):
        body = convert_leading_space_in_md(body)
    # Image-src sanitization now lives in Article.update_local_article so all
    # local-author entry points share it; no need to sanitize here.
    tags = _parse_tags(form.cleaned_data.get("tags", ""))
    article = Article.update_local_article(
        owner=request.user.identity,
        title=form.cleaned_data["title"],
        body=body,
        summary=form.cleaned_data.get("summary", "") or "",
        sensitive=bool(form.cleaned_data.get("sensitive", False)),
        visibility=form.cleaned_data["visibility"],
        language=request.user.language or "",
        tags=tags,
        article=article,
        share_to_mastodon=bool(form.cleaned_data.get("share_to_mastodon", False)),
    )
    record_activity("article", "web")
    return redirect(reverse("journal:article_retrieve", args=[article.uuid]))


def _article_last_modified(request, article_uuid: str):
    # Owner-level toggles (``anonymous_viewable``, ``restricted``) don't
    # bump piece ``edited_time``, so the visibility check must run here —
    # otherwise a privacy flip would leave anonymous clients with a
    # cached 200 served via 304.
    try:
        uid = get_uuid_or_404(article_uuid)
    except Http404:
        return None
    article = Article.objects.filter(uid=uid).select_related("owner").first()
    if not article or not article.is_visible_to(request.user):
        return None
    return article.edited_time


@require_http_methods(["GET", "HEAD"])
@conditional_get_for_anonymous(_article_last_modified)
def article_retrieve(request, article_uuid: str):
    article = get_object_or_404(Article, uid=get_uuid_or_404(article_uuid))
    if not article.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    if request.method == "HEAD":
        return HttpResponse()
    # AP clients reach the canonical Takahe Post via the
    # ``<link rel="alternate" type="application/activity+json">`` in
    # the rendered HTML head (see ``article.html``). The href there
    # points at ``latest_post.object_uri`` (the AP ``id``), so
    # Mastodon's HTML-fallback resolver lands on the right resource.
    return render(
        request,
        "article.html",
        {"article": article, "quotes_count": post_quotes_count(article.latest_post)},
    )


MAX_FEED_ITEMS = 10


class ArticleFeed(Feed):
    def __call__(self, request, *args, **kwargs):
        # redirect to the canonical handle if a linked handle is used
        try:
            linked_id = APIdentity.get_by_linked_handle(kwargs["username"])
            return redirect(linked_id.url + "feed/articles/", permanent=True)
        except ObjectDoesNotExist:
            return super().__call__(request, *args, **kwargs)

    def get_object(self, request, *args, **kwargs):
        o = APIdentity.get_by_handle(kwargs["username"])
        if not o.local:
            raise ObjectDoesNotExist(_("User not local"))
        if not o.user or not o.user.is_active:
            raise ObjectDoesNotExist(_("User not found"))
        activate_language_for_user(o.user)
        return o

    def title(self, owner):
        return (
            _("Articles by {0}").format(owner.display_name)
            if owner
            else _("Link invalid")
        )

    def link(self, owner: APIdentity):
        return owner.url if owner else settings.SITE_INFO["site_url"]

    def description(self, owner: APIdentity):
        if not owner:
            return _("Link invalid")
        elif not owner.anonymous_viewable:
            return _("Login required")
        else:
            return _("Articles by {0}").format(owner.display_name)

    def items(self, owner: APIdentity):
        if owner is None or not owner.anonymous_viewable:
            return []
        return Article.objects.filter(owner=owner, visibility=0).order_by(
            "-created_time"
        )[:MAX_FEED_ITEMS]

    def item_title(self, item: Article):
        s = item.title
        if item.sensitive:
            s += " " + _("(may contain sensitive content)")
        return s

    def item_description(self, item: Article):
        return item.html_content

    # item_link is only needed if NewsItem has no get_absolute_url method.
    def item_link(self, item: Article):
        return str(item.absolute_url)

    def item_categories(self, item: Article):
        return item.normalized_tags

    def item_pubdate(self, item: Article):
        return item.created_time

    def item_updateddate(self, item: Article):
        return item.edited_time

    def item_comments(self, item: Article):
        return item.absolute_url


@target_identity_required
def user_article_list(request: AuthedHttpRequest, user_name):
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
