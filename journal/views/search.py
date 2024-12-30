from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from common.models.misc import int_
from common.utils import PageLinksGenerator
from journal.models import JournalIndex, QueryParser


@login_required
def search(request):
    identity_id = request.user.identity.pk
    page = int_(request.GET.get("page"))
    q = QueryParser(request.GET.get("q", default=""))
    q.filter_by["owner_id"] = [identity_id]  # only search for current user
    q.filter_by["item_id"] = [">0"]  # only search for records with items
    index = JournalIndex.instance()
    r = index.search(
        q.q,
        filter_by=q.filter_by,
        query_by=q.query_by,
        sort_by="_text_match:desc",
        page=page,
    )
    return render(
        request,
        "search_journal.html",
        {
            "items": r.items,
            "pagination": PageLinksGenerator(r.page, r.pages, request.GET),
        },
    )
