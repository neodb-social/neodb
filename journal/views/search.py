from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from common.models.misc import int_
from common.utils import PageLinksGenerator

from ..models import Article
from ..search import JournalIndex, JournalQueryParser


@login_required
def search(request):
    page = int_(request.GET.get("page"), 1)
    q = JournalQueryParser(request.GET.get("q", default=""), page)
    q.filter_by_owner(request.user.identity)
    # Articles are item-less so they would be excluded by ``item_id > 0``;
    # only apply that gate when the caller did not explicitly target articles.
    selected_types = [t.lower() for t in q.filter_by.get("piece_class", [])]
    article_query = "article" in selected_types
    if not article_query:
        q.filter("item_id", ">0")
    if q:
        index = JournalIndex.instance()
        r = index.search(q)
        # Articles are item-less; ``r.items`` strips them. Surface them
        # via ``r.pieces`` so item-less hits actually render.
        articles = (
            [p for p in r.pieces if isinstance(p, Article)] if article_query else []
        )
        return render(
            request,
            "search_journal.html",
            {
                "items": r.items,
                "articles": articles,
                "pagination": PageLinksGenerator(r.page, r.pages, request.GET),
            },
        )
    else:
        return render(request, "search_journal.html", {"items": [], "articles": []})
