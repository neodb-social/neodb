"""Review reader/editor integration, including validation and publication."""

from typing import cast

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from lxml import html

from catalog.models import Edition
from journal.models import Mark, Review, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def writer():
    user = User.register(email="reviewwriter@example.com", username="reviewwriter")
    book = Edition.objects.create(title="The Reviewed Book")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, book, client


def test_review_reader_keeps_item_rating_and_review_actions(writer):
    user, book, client = writer
    Mark(user.identity, book).update(ShelfType.COMPLETE, "", 8)
    review = Review.update_item_review(
        book, user.identity, "A considered review", "## A section\n\nThoughts"
    )
    assert review is not None
    response = client.get(review.url)
    assert response.status_code == 200
    document = html.fromstring(response.content)
    assert "article-reading-page" in document.attrib["class"]
    assert document.xpath(f'//*[@id="review_{review.uuid}_title"]')
    assert document.xpath(f'//*[@id="review_{review.uuid}_content"]//h2')
    assert document.xpath(
        '//section[contains(@class,"review-subject")]//a[.="The Reviewed Book"]'
    )
    assert document.xpath('//*[contains(@class,"review-subject-rating")]')
    assert document.xpath(f'//a[@href="{review.edit_url}"]')
    assert document.xpath(f'//a[@href="{review.delete_url}"]')
    assert review.latest_post is not None
    assert document.xpath(f'//div[@id="replies_{review.latest_post.pk}"]')
    assert document.xpath(f'//div[@id="quote_{review.latest_post.pk}"]')
    more_url = reverse(
        "journal:user_review_list", args=[user.identity.handle, book.category]
    )
    assert document.xpath(f'//a[@href="{more_url}"]')
    assert not document.xpath("//aside")
    assert not document.xpath('//img[@class="article-cover"]')


def test_review_create_has_item_context_and_explicit_publish(writer):
    _, book, client = writer
    document = html.fromstring(
        client.get(reverse("journal:review_create", args=[book.uuid])).content
    )
    assert document.xpath('//button[@type="submit"][contains(.,"Publish review")]')
    assert document.xpath('//button[@data-article-preview][@type="button"]')
    assert document.xpath('//section[contains(@class,"review-subject")]')
    date_field = document.find('.//input[@id="id_mark_date"]')
    assert date_field is not None
    assert date_field.get("value") == timezone.localdate().isoformat()
    assert not Review.objects.exists()


@pytest.mark.parametrize("date", ["", "not-a-date", "1899-12-31", "2999-01-01"])
def test_review_validation_keeps_writing_and_date_settings(writer, date):
    _, book, client = writer
    response = client.post(
        reverse("journal:review_create", args=[book.uuid]),
        {
            "item": book.pk,
            "title": "Keep this title",
            "body": "Keep this writing",
            "visibility": "0",
            "language": "en",
            "mark_anotherday": "on",
            "mark_date": date,
        },
    )
    assert response.status_code == 400
    document = html.fromstring(response.content)
    assert document.xpath('//div[@role="alert"]')
    assert document.xpath('//details[@id="article-publish-settings"][@open]')
    assert (
        cast(str, document.xpath('string(//textarea[@id="id_body"])')).lstrip("\n")
        == "Keep this writing"
    )
    assert (
        cast(str, document.xpath('string(//textarea[@id="id_title"])')).lstrip("\n")
        == "Keep this title"
    )
    assert document.xpath('//*[@data-review-date-field]//ul[@class="errorlist"]')
    assert not Review.objects.exists()


def test_review_publish_and_edit_preserve_date_language_and_privacy(writer):
    user, book, client = writer
    data = {
        "item": book.pk,
        "title": "First review",
        "body": "Thoughts",
        "visibility": "2",
        "language": "fr",
        "mark_anotherday": "on",
        "mark_date": "2020-04-05",
    }
    response = client.post(reverse("journal:review_create", args=[book.uuid]), data)
    assert response.status_code == 302
    review = Review.objects.get(owner=user.identity, item=book)
    assert timezone.localtime(review.created_time).date().isoformat() == "2020-04-05"
    assert review.latest_post is not None
    assert review.latest_post.language == "fr"
    post_id = review.latest_post.pk
    edit = client.get(review.edit_url)
    assert "Save changes" in edit.content.decode()
    data.update(title="Revised review", body="Updated thoughts")
    data.pop("mark_anotherday")
    data.pop("mark_date")
    assert client.post(review.edit_url, data).status_code == 302
    review.refresh_from_db()
    assert review.title == "Revised review"
    assert review.latest_post is not None
    assert review.latest_post.pk == post_id
    assert review.visibility == 2
    assert timezone.localtime(review.created_time).date().isoformat() == "2020-04-05"
    assert Client().get(review.url).status_code in (403, 404)


@pytest.mark.parametrize("kind", ["article", "review"])
@pytest.mark.parametrize(
    "title, expected",
    [
        ("Part 1\r\nThe start\nNext\u2028Section", "Part 1 The start Next Section"),
        ("x" * 249 + "\r\n" + "y" * 250, "x" * 249 + " " + "y" * 250),
    ],
)
def test_editor_normalizes_title_before_length_validation(
    writer, kind, title, expected
):
    from journal.models import Article

    user, book, client = writer
    url = (
        reverse("journal:article_compose")
        if kind == "article"
        else reverse("journal:review_create", args=[book.uuid])
    )
    response = client.post(
        url, {"item": book.pk, "title": title, "body": "Text", "visibility": "0"}
    )
    assert response.status_code == 302
    model = Article if kind == "article" else Review
    piece = model.objects.get(owner=user.identity)
    assert piece.title == expected
    assert piece.ap_object["name"] == expected


@pytest.mark.parametrize("kind", ["article", "review"])
def test_editor_rejects_overlong_title_with_field_error(writer, kind):
    _, book, client = writer
    url = (
        reverse("journal:article_compose")
        if kind == "article"
        else reverse("journal:review_create", args=[book.uuid])
    )
    response = client.post(
        url,
        {
            "item": book.pk,
            "title": "x" * 501,
            "body": "Keep my writing",
            "visibility": "0",
        },
    )
    assert response.status_code == 400
    assert "title" in response.context["form"].errors
    assert response.context["form"]["title"].field.max_length == 500


def test_unchecked_review_date_ignores_stale_invalid_value(writer):
    _, book, client = writer
    response = client.post(
        reverse("journal:review_create", args=[book.uuid]),
        {
            "item": book.pk,
            "title": "No custom date",
            "body": "Text",
            "visibility": "0",
            "mark_date": "not-a-date",
        },
    )
    assert response.status_code == 302


@pytest.mark.parametrize("ignored_date", ["not-a-date", "1899-12-31", "2020-04-05"])
def test_review_date_can_be_selected_after_invalid_submission_without_js(
    writer, ignored_date
):
    user, book, client = writer
    url = reverse("journal:review_create", args=[book.uuid])
    data = {
        "item": book.pk,
        "title": "",
        "body": "Keep my writing",
        "visibility": "0",
        "mark_date": ignored_date,
    }
    response = client.post(url, data)
    assert response.status_code == 400
    assert "title" in response.context["form"].errors
    assert "mark_date" not in response.context["form"].errors
    document = html.fromstring(response.content)
    date_field = document.find('.//input[@id="id_mark_date"]')
    assert date_field is not None
    # A browser without JS must be able to enter and submit a date on retry.
    assert "disabled" not in date_field.attrib
    assert date_field.get("value", "") == ""
    assert response.wsgi_request.POST["mark_date"] == ignored_date

    data.update(title="Corrected title", mark_anotherday="on", mark_date="2021-03-04")
    assert client.post(url, data).status_code == 302
    review = Review.objects.get(owner=user.identity, item=book)
    assert review.body == "Keep my writing"
    assert timezone.localtime(review.created_time).date().isoformat() == "2021-03-04"
