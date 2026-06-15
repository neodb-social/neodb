import pytest
import requests
from django.test import Client
from django.urls import reverse

from catalog.models import Edition, Movie
from journal.models import Article, Collection, Mark, Review, ShelfType, Tag
from users.models import User


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_post_review_collection_and_profile_pages(live_server):
    book = Edition.objects.create(title="Web Page Book")
    user = User.register(email="web@example.com", username="webuser")
    response = requests.get(f"{live_server.url}{user.identity.url}", timeout=5)
    assert response.status_code == 200

    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    auth_cookies = {key: morsel.value for key, morsel in authed_client.cookies.items()}
    response = requests.get(
        f"{live_server.url}{user.identity.url}", cookies=auth_cookies, timeout=5
    )
    assert response.status_code == 200

    Mark(user.identity, book).update(ShelfType.WISHLIST, "note", None, [], 0)
    m = Mark(user.identity, book).shelfmember
    assert m is not None
    post = m.latest_post
    assert post is not None
    response = requests.get(
        f"{live_server.url}/@{user.identity.handle}/posts/{post.pk}/", timeout=5
    )
    assert response.status_code == 200

    review = Review.update_item_review(
        book,
        user.identity,
        "Web Review",
        "Review body",
        visibility=0,
    )
    assert review is not None
    response = requests.get(f"{live_server.url}{review.url}", timeout=5)
    assert response.status_code == 200

    collection = Collection.objects.create(
        owner=user.identity,
        title="Web Collection",
        brief="",
        visibility=0,
    )
    collection.append_item(book)
    response = requests.get(f"{live_server.url}{collection.url}", timeout=5)
    assert response.status_code == 200

    collection2 = Collection.objects.create(
        owner=user.identity,
        title="Dynamic Collection",
        brief="",
        visibility=0,
        query="status:wishlist",
    )
    response = requests.get(f"{live_server.url}{collection2.url}", timeout=5)
    assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
def test_tag_pages_category_filter():
    user = User.register(email="tagpage@example.com", username="tagpageuser")
    book = Edition.objects.create(title="Tag Page Book")
    movie = Movie.objects.create(title="Tag Page Movie")
    tag = Tag.objects.create(owner=user.identity, title="mixed", visibility=0)
    tag.append_item(book)
    tag.append_item(movie)

    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    handle = user.identity.handle

    list_url = reverse("journal:user_tag_list", args=[handle])
    assert client.get(list_url).status_code == 200
    assert client.get(list_url, {"category": "book"}).status_code == 200
    # an invalid category is ignored, not an error
    assert client.get(list_url, {"category": "bogus"}).status_code == 200

    # The category dropdown must render real option values/labels even on a
    # repeat request, when visible_categories is served from the session cache
    # as plain strings (regression: dropdown rendered blank/empty options).
    response = client.get(list_url)
    assert response.status_code == 200
    content = response.content.decode()
    select_html = content.split('id="category"', 1)[1].split("</select>", 1)[0]
    assert 'value="book"' in select_html
    assert "Book" in select_html
    assert 'value="people"' not in select_html
    assert 'value="collection"' not in select_html

    member_url = reverse("journal:user_tag_member_list", args=[handle, "mixed"])
    response = client.get(member_url)
    assert response.status_code == 200
    assert b"Tag Page Book" in response.content
    assert b"Tag Page Movie" in response.content

    response = client.get(member_url, {"category": "book"})
    assert response.status_code == 200
    assert b"Tag Page Book" in response.content
    assert b"Tag Page Movie" not in response.content


@pytest.mark.django_db(databases="__all__")
def test_profile_articles_shelf_preview():
    user = User.register(email="artshelf@example.com", username="artshelfuser")
    identity = user.identity
    Article.update_local_article(
        owner=identity,
        title="First Article",
        body="Body of the first article with several words to excerpt.",
        tags=["alpha"],
        visibility=0,
    )
    Article.update_local_article(
        owner=identity,
        title="Second Article",
        body="Another article body.",
        visibility=0,
    )

    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    handle = identity.handle

    # The shelf preview partial renders recent articles in a compact list.
    preview_url = reverse("journal:profile_articles", args=[handle])
    response = client.get(preview_url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "First Article" in content
    assert "Second Article" in content
    assert 'class="article-preview"' in content
    # see-all count reflects the visible total
    assert ">2</a>" in content

    # The profile page lazy-loads the shelf via htmx rather than a bare link.
    profile_response = client.get(identity.url)
    assert profile_response.status_code == 200
    assert preview_url in profile_response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_profile_articles_shelf_empty_hidden_for_visitor():
    owner = User.register(email="artempty@example.com", username="artemptyuser")
    visitor = User.register(email="artvisitor@example.com", username="artvisitor")

    client = Client()
    client.force_login(visitor, backend="mastodon.auth.OAuth2Backend")
    preview_url = reverse("journal:profile_articles", args=[owner.identity.handle])
    response = client.get(preview_url)
    assert response.status_code == 200
    # With no articles, a visitor's shelf collapses itself.
    assert "hide closest .shelf" in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_profile_articles_shelf_hidden_from_anonymous_when_not_viewable():
    owner = User.register(email="artprivate@example.com", username="artprivateuser")
    identity = owner.identity
    identity.anonymous_viewable = False
    identity.save(update_fields=["anonymous_viewable"])
    Article.update_local_article(
        owner=identity, title="Hidden Article", body="secret", visibility=0
    )

    # An anonymous visitor gets an empty response (no article data leaked).
    preview_url = reverse("journal:profile_articles", args=[identity.handle])
    response = Client().get(preview_url)
    assert response.status_code == 200
    assert response.content == b""
