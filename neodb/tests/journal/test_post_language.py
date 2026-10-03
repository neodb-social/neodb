import pytest
from django.urls import reverse

from catalog.models import Edition
from journal.models import Article, Mark, Note, Review, ShelfType
from takahe.utils import Takahe
from users.models import User


def _mark_post_data(**extra):
    data = {
        "status": "complete",
        "rating_grade": "8",
        "text": "great",
        "visibility": "0",
        "tags": "",
        "mark_date": "",
    }
    data.update(extra)
    return data


def _mark_post(user: User, book: Edition):
    shelfmember = Mark(user.identity, book).shelfmember
    assert shelfmember is not None
    post = shelfmember.latest_post
    assert post is not None
    return post


@pytest.mark.django_db(databases="__all__")
def test_mark_language_is_saved_and_kept(client):
    user = User.register(email="lang-mark@example.com", username="langmark")
    book = Edition.objects.create(title="Language Book")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    url = reverse("journal:mark", args=[book.uuid])

    client.post(url, _mark_post_data(language="ja"))
    assert _mark_post(user, book).language == "ja"

    response = client.get(url)
    assert response.context["form"].initial["language"] == "ja"

    # a save that carries no language, e.g. from the API, keeps the chosen one
    Mark(user.identity, book).update(ShelfType.PROGRESS, "still reading", 8)
    assert _mark_post(user, book).language == "ja"


@pytest.mark.django_db(databases="__all__")
def test_mark_language_defaults_and_unknown(client):
    user = User.register(email="lang-default@example.com", username="langdefault")
    book = Edition.objects.create(title="Default Language Book")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    url = reverse("journal:mark", args=[book.uuid])

    response = client.get(url)
    assert response.context["form"].initial["language"] == user.macrolanguage

    client.post(url, _mark_post_data())
    assert _mark_post(user, book).language == user.macrolanguage

    client.post(url, _mark_post_data(language="x"))
    assert _mark_post(user, book).language == ""
    response = client.get(url)
    assert response.context["form"].initial["language"] == "x"


@pytest.mark.django_db(databases="__all__")
def test_mark_language_rejects_unknown_code(client):
    user = User.register(email="lang-bad@example.com", username="langbad")
    book = Edition.objects.create(title="Bad Language Book")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    response = client.post(
        reverse("journal:mark", args=[book.uuid]),
        _mark_post_data(language="not-a-language"),
    )
    assert response.status_code == 400
    assert Mark(user.identity, book).shelf_type is None


@pytest.mark.django_db(databases="__all__")
def test_review_language(client):
    user = User.register(email="lang-review@example.com", username="langreview")
    book = Edition.objects.create(title="Reviewed Book")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    response = client.get(reverse("journal:review_create", args=[book.uuid]))
    assert response.context["form"].initial["language"] == user.macrolanguage

    client.post(
        reverse("journal:review_create", args=[book.uuid]),
        {
            "item": book.pk,
            "title": "A review",
            "body": "Some words",
            "visibility": "0",
            "language": "fr",
        },
    )
    review = Review.objects.get(owner=user.identity, item=book)
    assert review.latest_post is not None
    assert review.latest_post.language == "fr"

    response = client.get(reverse("journal:review_edit", args=[book.uuid, review.uuid]))
    assert response.context["form"].initial["language"] == "fr"


@pytest.mark.django_db(databases="__all__")
def test_article_language(client):
    user = User.register(email="lang-article@example.com", username="langarticle")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    response = client.get(reverse("journal:article_compose"))
    assert response.context["form"].initial["language"] == user.language

    client.post(
        reverse("journal:article_compose"),
        {
            "title": "An article",
            "body": "Article body",
            "visibility": "0",
            "language": "de",
        },
    )
    article = Article.objects.get(owner=user.identity)
    assert article.language == "de"
    assert article.latest_post is not None
    assert article.latest_post.language == "de"

    response = client.get(reverse("journal:article_edit", args=[article.uuid]))
    assert response.context["form"].initial["language"] == "de"


@pytest.mark.django_db(databases="__all__")
def test_note_language(client):
    user = User.register(email="lang-note@example.com", username="langnote")
    book = Edition.objects.create(title="Noted Book")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    client.post(
        reverse("journal:note", args=[book.uuid]),
        {"mode": "note", "content": "a note", "visibility": "0", "language": "ko"},
    )
    note = Note.objects.get(owner=user.identity, item=book)
    assert note.latest_post is not None
    assert note.latest_post.language == "ko"


@pytest.mark.django_db(databases="__all__")
def test_post_compose_and_edit_language(client):
    user = User.register(email="lang-post@example.com", username="langpost")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    html = client.get(reverse("journal:post_compose")).content.decode()
    assert 'name="language"' in html
    assert 'name="visibility"' in html

    post = Takahe.post(
        user.identity.pk, "hello", Takahe.Visibilities.public, language=""
    )
    assert post is not None
    response = client.get(reverse("journal:post_edit", args=[post.pk]))
    assert response.context["user_language"] == "x"
