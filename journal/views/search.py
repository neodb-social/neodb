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
    # Exclude orphan ``Post``-class docs (timeline posts with no linked
    # journal piece) but let item-less pieces like ``Article`` through.
    # The previous ``item_id > 0`` gate was a coarse stand-in for this and
    # misclassified articles as orphans — tag links from /article/<uuid>
    # silently returned no hits.
    q.exclude("piece_class", "Post")
    if q:
        index = JournalIndex.instance()
        r = index.search(q)
        # Articles are item-less; ``r.items`` strips them. Surface them
        # via ``r.pieces`` so item-less hits actually render alongside
        # item-keyed pieces (matters for tag / free-text searches now
        # that the gate isn't ``type:article``-only).
        articles = [p for p in r.pieces if isinstance(p, Article)]
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
