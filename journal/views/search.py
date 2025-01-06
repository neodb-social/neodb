from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from common.models.misc import int_
from common.utils import PageLinksGenerator
from journal.models import JournalIndex, JournalQueryParser


@login_required
def search(request):
    identity_id = request.user.identity.pk
    page = int_(request.GET.get("page"), 1)
    q = JournalQueryParser(request.GET.get("q", default=""), page)
    q.filter("item_id", ">0")
    q.filter("owner_id", identity_id)
    if q:
        index = JournalIndex.instance()
        r = index.search(q)
        return render(
            request,
            "search_journal.html",
            {
                "items": r.items,
                "pagination": PageLinksGenerator(r.page, r.pages, request.GET),
            },
        )
    else:
        return render(request, "search_journal.html", {"items": []})
