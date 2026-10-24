import re
from collections import Counter
from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.syndication.views import Feed
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from common.models.lang import translate
from common.models.misc import int_
from common.sentry import record_activity
from common.utils import (
    AuthedHttpRequest,
    CustomPaginator,
    PageLinksGenerator,
    get_uuid_or_404,
    target_identity_required,
)
from users.middlewares import activate_language_for_user
from users.models.apidentity import APIdentity

from ..forms import ArticleForm
from ..models import Article
from ..models.article import _normalize_tags
from ..models.common import q_owned_piece_visible_to_user
from ..models.renderers import convert_leading_space_in_md
from .common import (
    conditional_get_for_anonymous,
    piece_handle_matches,
    post_quotes_count,
    require_piece_handle,
)


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t and t.strip()]


def _article_tag_suggestions(owner) -> list[str]:
    # Only the signed-in author's vocabulary, including their private articles.
    # Fetch each stored tag set once, even for authors with long histories.
    # DISTINCT ON picks its newest article; the outer query restores recency.
    latest_tag_sets = (
        Article.objects.filter(owner=owner)
        .order_by("tags", "-edited_time", "-id")
        .distinct("tags")
        .values("pk")
    )
    suggestions = {}
    for tags in (
        Article.objects.filter(pk__in=latest_tag_sets)
        .order_by("-edited_time", "-id")
        .values_list("tags", flat=True)
        .iterator()
    ):
        if not isinstance(tags, list):
            continue
        for tag in _normalize_tags(tags):
            suggestions[tag] = None
            if len(suggestions) == 100:
                return list(suggestions)
    return list(suggestions)


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
                "language": article.language or "x",
                "share_to_mastodon": False,
            }
            form = ArticleForm(instance=article, initial=initial)
        else:
            form = ArticleForm(
                initial={
                    "language": request.user.language,
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
                "tag_suggestions": _article_tag_suggestions(request.user.identity),
            },
        )
    form = (
        ArticleForm(request.POST, request.FILES, instance=article)
        if article
        else ArticleForm(request.POST, request.FILES)
    )
    if not form.is_valid():
        # Re-render with the bound form so users see field-level errors
        # instead of a generic 400 page.
        return render(
            request,
            "article_edit.html",
            {
                "form": form,
                "article": article,
                "tag_suggestions": _article_tag_suggestions(request.user.identity),
            },
            status=400,
        )
    body = form.cleaned_data["body"]
    if form.cleaned_data.get("leading_space"):
        body = convert_leading_space_in_md(body)
    # Image-src sanitization now lives in Article.update_local_article so all
    # local-author entry points share it; no need to sanitize here.
    tags = _parse_tags(form.cleaned_data.get("tags", ""))
    language = form.cleaned_data["language"]
    if language is None:
        language = article.language if article else request.user.language
    article = Article.update_local_article(
        owner=request.user.identity,
        title=form.cleaned_data["title"],
        body=body,
        summary=form.cleaned_data.get("summary", "") or "",
        sensitive=bool(form.cleaned_data.get("sensitive", False)),
        visibility=form.cleaned_data["visibility"],
        language=language or "",
        tags=tags,
        article=article,
        share_to_mastodon=bool(form.cleaned_data.get("share_to_mastodon", False)),
        cover=form.cleaned_data.get("cover"),
    )
    record_activity("article", "web")
    return redirect(reverse("journal:article_retrieve", args=[article.uuid]))


def _article_last_modified(request, article_uuid: str, user_name: str | None = None):
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
    # a handle that doesn't own the article renders 404, not a 304
    if not piece_handle_matches(article, user_name):
        return None
    return article.edited_time


@require_http_methods(["GET", "HEAD"])
@conditional_get_for_anonymous(_article_last_modified)
def article_retrieve(request, article_uuid: str, user_name: str | None = None):
    article = get_object_or_404(Article, uid=get_uuid_or_404(article_uuid))
    # checked before visibility so a mismatched handle cannot tell a
    # private article apart from a missing one
    require_piece_handle(article, user_name)
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


@require_http_methods(["POST"])
@login_required
def article_translate(request, article_uuid: str):
    article = get_object_or_404(Article, uid=get_uuid_or_404(article_uuid))
    if not article.is_visible_to(request.user):
        raise PermissionDenied(_("Insufficient permission"))
    text = article.html_content
    if article.latest_post:
        lang = article.latest_post.language
    elif article.owner.local:
        lang = article.owner.user.language
    else:
        lang = None
    target_lang = request.user.language or "en"
    text = translate(text, target_lang, lang)
    title = translate(article.title, target_lang, lang)
    return HttpResponse(
        f'<span hx-swap-oob="true" id="article_{article.uuid}_title">{escape(title)}</span><div>{text}</div>'
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
    queryset = (
        Article.objects.filter(owner=target)
        .filter(q_owned_piece_visible_to_user(request.user, target))
        # ``id`` breaks ties: two articles saved in the same tick would
        # otherwise be free to swap places between page requests.
        .order_by("-created_time", "-id")
    )
    # Aggregate repeated tag sets in SQL, then use the same canonicalization
    # as article links (including deduplication and the 30-tag limit). Expanding
    # raw tags and counting them would overcount legacy spelling variants.
    raw_selected_tag = request.GET.get("tag", "").strip()
    selected_tag = next(iter(_normalize_tags([raw_selected_tag])), raw_selected_tag)
    tag_counts = Counter()
    matching_tag_sets = []
    for row in (
        queryset.order_by().values("tags").annotate(total=Count("id")).iterator()
    ):
        tags = _normalize_tags(row["tags"]) if isinstance(row["tags"], list) else []
        tag_counts.update(dict.fromkeys(tags, row["total"]))
        if selected_tag and selected_tag in tags:
            matching_tag_sets.append(row["tags"])
    archive_years = {}
    months = (
        queryset.order_by()
        .annotate(month=TruncMonth("created_time"))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("-month")
    )
    for row in months:
        month = row["month"]
        year = archive_years.setdefault(
            month.year, {"value": str(month.year), "months": []}
        )
        year["months"].append(
            {"value": month.strftime("%Y-%m"), "label": formats.date_format(month, "F")}
        )
    selected_date = request.GET.get("date", "")
    date_label = ""
    match = re.fullmatch(r"([0-9]{4})(?:-(0[1-9]|1[0-2]))?", selected_date)
    if match and 0 < int(match[1]) < 9999:
        year, month = int(match[1]), int(match[2]) if match[2] else None
        queryset = queryset.filter(created_time__year=year)
        if month:
            queryset = queryset.filter(created_time__month=month)
        date_label = formats.date_format(
            date(year, month or 1, 1), "YEAR_MONTH_FORMAT" if month else "Y"
        )
    else:
        selected_date = ""
    if selected_tag:
        queryset = queryset.filter(tags__in=matching_tag_sets)
    paginator = CustomPaginator(queryset, request)
    # ``get_page`` never raises: it clamps an out-of-range number to the last
    # page and an unparseable one to the first. The links are therefore built
    # from the page it returned, not from what was asked for, so that what is
    # highlighted is what is on screen.
    page = paginator.get_page(int_(request.GET.get("page", default=1), 1))
    articles = list(page)
    return render(
        request,
        "user_article_list.html",
        {
            "user": target.user,
            "identity": target,
            "articles": articles,
            "total": paginator.count,
            "article_tags": [
                {"name": tag, "count": count}
                for tag, count in sorted(
                    tag_counts.items(), key=lambda pair: (-pair[1], pair[0].casefold())
                )
            ],
            "archive_years": list(archive_years.values()),
            "selected_tag": selected_tag,
            "selected_date": selected_date,
            "date_label": date_label,
            "is_filtered": bool(selected_tag or selected_date),
            "show_featured": page.number == 1
            and not selected_tag
            and not selected_date,
            # one page needs no page links, and the template hides the block
            # when there is no generator
            "pagination": PageLinksGenerator(
                page.number, paginator.num_pages, request.GET
            )
            if paginator.num_pages > 1
            else None,
        },
    )
