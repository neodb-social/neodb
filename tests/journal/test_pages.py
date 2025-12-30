import pytest
import requests
from django.test import Client

from catalog.models import Edition
from journal.models import Collection, Mark, Review, ShelfType
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
