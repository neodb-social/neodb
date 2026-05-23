import pytest

from activities.models import Post


@pytest.fixture(autouse=True)
def _enable_federation(settings):
    original = settings.SETUP.NO_FEDERATION
    settings.SETUP.NO_FEDERATION = False
    yield
    settings.SETUP.NO_FEDERATION = original


@pytest.mark.django_db
def test_replies_collection_endpoint(identity, client):
    """The replies collection endpoint should return AP JSON with reply URIs."""
    parent = Post.objects.create(
        author=identity,
        content="Parent post",
        local=True,
        visibility=Post.Visibilities.public,
    )
    parent.object_uri = parent.urls.object_uri
    parent.url = parent.absolute_object_uri()
    parent.save()

    reply = Post.objects.create(
        author=identity,
        content="A reply",
        local=True,
        in_reply_to=parent.object_uri,
        visibility=Post.Visibilities.public,
    )
    reply.object_uri = reply.urls.object_uri
    reply.save()

    response = client.get(
        f"/@{identity.handle}/posts/{parent.pk}/replies/",
        HTTP_ACCEPT="application/activity+json",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "Collection"
    assert data["totalItems"] >= 1
    first_page = data["first"]
    assert first_page["type"] == "CollectionPage"
    assert reply.object_uri in first_page["items"]


@pytest.mark.django_db
def test_replies_collection_excludes_private(identity, other_identity, client):
    """The replies collection should only include public/unlisted replies."""
    parent = Post.objects.create(
        author=identity,
        content="Parent post",
        local=True,
        visibility=Post.Visibilities.public,
    )
    parent.object_uri = parent.urls.object_uri
    parent.url = parent.absolute_object_uri()
    parent.save()

    private_reply = Post.objects.create(
        author=other_identity,
        content="Private reply",
        local=True,
        in_reply_to=parent.object_uri,
        visibility=Post.Visibilities.followers,
    )
    private_reply.object_uri = private_reply.urls.object_uri
    private_reply.save()

    response = client.get(
        f"/@{identity.handle}/posts/{parent.pk}/replies/",
        HTTP_ACCEPT="application/activity+json",
    )
    assert response.status_code == 200
    data = response.json()
    first_page = data["first"]
    assert private_reply.object_uri not in first_page["items"]


@pytest.mark.django_db
def test_replies_collection_404_nonlocal(identity, client):
    """The replies collection should 404 for non-local posts."""
    post = Post.objects.create(
        author=identity,
        content="Remote post",
        local=False,
        object_uri="https://remote.test/posts/1",
        visibility=Post.Visibilities.public,
    )
    response = client.get(
        f"/@{identity.handle}/posts/{post.pk}/replies/",
        HTTP_ACCEPT="application/activity+json",
    )
    assert response.status_code == 404
