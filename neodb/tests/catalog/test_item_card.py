"""The list item card: rating row, by-line, site labels and the mark footer."""

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

from catalog.models import Edition, ExternalResource, IdType, People, PeopleType
from journal.models import Mark, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _render(template: str, user=None, **context) -> str:
    request = RequestFactory().get("/")
    request.user = user or AnonymousUser()
    return render_to_string(template, {"request": request, **context})


def _book() -> Edition:
    book = Edition.objects.create(
        title="Children of Time",
        author=["Adrian Tchaikovsky"],
        translator=["Some Translator"],
    )
    book.sync_credits_from_metadata()
    ExternalResource.objects.create(
        item=book,
        id_type=IdType.Goodreads,
        id_value="25499718",
        url="https://www.goodreads.com/book/show/25499718",
    )
    return Edition.objects.get(pk=book.pk)


def test_rating_row_keeps_count_and_hides_in_solo_mode():
    book = _book()
    book.rating_info = {"average": 8.6, "count": 15}

    html = _render("_item_card.html", item=book)

    row = re.search(r'<div class="card-rating solo-hidden">(.*?)</div>', html, re.S)
    assert row is not None
    assert "★ 8.6" in row.group(1)
    assert "15 ratings" in row.group(1)


def test_no_rating_row_below_the_minimum_or_on_people():
    book = _book()
    book.rating_info = {"average": None, "count": 2}
    person = People.objects.create(
        metadata={"localized_name": [{"lang": "en", "text": "A Person"}]},
        people_type=PeopleType.PERSON,
    )
    person.rating_info = {"average": 9.0, "count": 20}

    assert "card-rating" not in _render("_item_card.html", item=book)
    assert "card-rating" not in _render("_item_card.html", item=person)


def test_lead_credit_has_no_label_and_site_labels_leave_the_title():
    html = _render("_item_card.html", item=_book())

    brief = re.search(r'<div class="brief">(.*?)</div>\s*</div>', html, re.S)
    assert brief is not None
    assert "Adrian Tchaikovsky" in brief.group(1)
    assert "author:" not in brief.group(1)
    assert "translator:" in brief.group(1)
    h5 = re.search(r"<h5>(.*?)</h5>", html, re.S)
    assert h5 is not None
    assert "site-list" not in h5.group(1)
    assert re.search(r"</h5>\s*<small>.*site-list.*</small>\s*</hgroup>", html, re.S)
    assert 'class="dc-cover tall"' in html


def test_mark_footer_shows_status_pill_date_and_comment():
    user = User.register(email="card@example.com", username="carduser")
    book = _book()
    mark = Mark(user.identity, book)
    mark.update(ShelfType.COMPLETE, comment_text="great read", rating_grade=8)
    mark = Mark(user.identity, book)

    html = _render("_list_item_mark.html", user=user, item=book, mark=mark)

    assert f'data-mark-detail="{book.uuid}"' in html
    assert re.search(r'<span class="mark-status">\s*Read\s*</span>', html)
    assert 'class="mark-date"' in html
    assert re.search(r'<div class="mark-comment">.*great read.*</div>', html, re.S)
