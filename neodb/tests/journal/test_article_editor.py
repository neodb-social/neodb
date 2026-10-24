"""Article editor validation and publishing, through the real form/view."""

import json
from typing import cast

import pytest
from django.test import Client
from django.urls import reverse
from lxml import html

from journal.models import Article
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


@pytest.fixture
def author():
    user = User.register(email="writer@example.com", username="writer")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def test_compose_has_explicit_publish_controls_without_creating_article(author):
    _, client = author
    response = client.get(reverse("journal:article_compose"))
    document = html.fromstring(response.content)
    assert document.xpath('//button[@type="submit"][contains(., "Publish article")]')
    assert document.xpath('//button[@data-article-preview][@type="button"]')
    assert document.xpath('//details[@id="article-publish-settings"]')
    assert not Article.objects.exists()


def test_invalid_form_preserves_text_and_displays_errors(author):
    _, client = author
    response = client.post(
        reverse("journal:article_compose"),
        {
            "title": "",
            "body": "Keep my writing",
            "summary": "Keep my summary",
            "tags": "books",
            "visibility": "99",
            "language": "en",
        },
    )
    assert response.status_code == 400
    document = html.fromstring(response.content)
    assert document.xpath('//div[@role="alert"]')
    assert document.xpath('//details[@id="article-publish-settings"][@open]')
    assert "Keep my writing" in cast(
        str, document.xpath('string(//textarea[@id="id_body"])')
    )
    assert cast(float, document.xpath('count(//ul[@class="errorlist"])')) >= 2
    assert not Article.objects.exists()


def test_publish_and_edit_keep_visibility_and_existing_post(author):
    user, client = author
    data = {
        "title": "First",
        "body": "Original",
        "summary": "A summary",
        "tags": "books, life",
        "visibility": "2",
        "language": "en",
    }
    response = client.post(reverse("journal:article_compose"), data)
    assert response.status_code == 302
    article = Article.objects.get(owner=user.identity)
    assert article.latest_post is not None
    post_id = article.latest_post.pk
    assert article.visibility == 2
    assert article.normalized_tags == ["books", "life"]
    edit_url = reverse("journal:article_edit", args=[article.uuid])
    assert "Save changes" in client.get(edit_url).content.decode()
    data.update(title="Revised", body="Updated")
    assert client.post(edit_url, data).status_code == 302
    article.refresh_from_db()
    assert article.title == "Revised"
    assert article.latest_post is not None
    assert article.latest_post.pk == post_id
    assert article.visibility == 2
    assert Client().get(article.url).status_code in (403, 404)


def test_sensitive_reader_keeps_cover_and_contents_inside_reveal(author):
    user, client = author
    article = Article.update_local_article(
        owner=user.identity,
        title="Sensitive",
        body="## Private section\n\nText",
        visibility=0,
        sensitive=True,
        tags=["R&D"],
    )
    document = html.fromstring(client.get(article.url).content)
    content = document.find(".//*[@data-article-content]")
    assert content is not None
    assert "hidden" in content.attrib
    assert content.xpath('.//h2[.="Private section"]')
    assert document.xpath('//button[contains(@class,"article-reveal")]')
    assert document.xpath('//a[contains(@href,"?tag=R%26D")]')
    assert not document.xpath("//aside")
    assert document.xpath(f'//a[@href="{article.delete_url}"]')


def test_tag_picker_only_suggests_authors_tags_and_escapes_script_data(author):
    user, client = author
    other = User.register(email="otherwriter@example.com", username="otherwriter")
    Article.objects.create(
        owner=other.identity, title="Other", body="Text", tags=["not-my-tag"]
    )
    Article.objects.create(
        owner=user.identity,
        title="Private",
        body="Text",
        visibility=2,
        tags=["reading", "reading", "</script><script>alert(1)</script>"],
    )
    response = client.get(reverse("journal:article_compose"))
    document = html.fromstring(response.content)
    payload = cast(
        str, document.xpath('string(//script[@id="article-tag-suggestions"])')
    )
    assert json.loads(payload) == ["reading", "</script><script>alert(1)</script>"]
    assert "</script>" not in payload
    assert "not-my-tag" not in response.content.decode()
    # The original input still supports publishing without JavaScript.
    assert document.xpath('//input[@name="tags"][@type="text"]')


def test_invalid_article_keeps_picker_suggestions_and_selected_tags(author):
    user, client = author
    Article.objects.create(
        owner=user.identity, title="Existing", body="Text", tags=["reading"]
    )
    response = client.post(
        reverse("journal:article_compose"),
        {"title": "", "body": "Keep", "visibility": "0", "tags": "reading, new tag"},
    )
    assert response.status_code == 400
    document = html.fromstring(response.content)
    field = document.find('.//input[@name="tags"]')
    assert field is not None
    assert field.get("value") == "reading, new tag"
    assert json.loads(
        cast(str, document.xpath('string(//script[@id="article-tag-suggestions"])'))
    ) == ["reading"]


def test_remote_author_reader_does_not_link_to_anonymous_forbidden_archive(author):
    user, _ = author
    article = Article.update_local_article(
        owner=user.identity,
        title="Remote reader",
        body="Text",
        visibility=0,
        tags=["reading"],
    )
    identity = user.identity
    identity.local = False
    identity.save(update_fields=["local"])
    response = Client().get(article.url)
    assert response.status_code == 200
    document = html.fromstring(response.content)
    archive = reverse("journal:user_article_list", args=[identity.handle])
    assert not document.xpath(f'//a[starts-with(@href,"{archive}")]')
    assert document.xpath('//ul[@class="article-story-tags"]/li[.="reading"]')
