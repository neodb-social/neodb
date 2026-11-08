import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from PIL import Image


@pytest.fixture
def _media_root(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "https://media.example.com/media/"


def _image(format: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(buffer, format=format)
    return buffer.getvalue()


def _patch_multipart(client, data: dict):
    return client.patch(
        "/api/v1/accounts/update_credentials",
        data=encode_multipart(BOUNDARY, data),
        content_type=MULTIPART_CONTENT,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("_media_root")
def test_update_credentials_accepts_heic(api_client, identity):
    response = _patch_multipart(
        api_client,
        {
            "avatar": SimpleUploadedFile("p.heic", _image("HEIF"), "image/heic"),
            "header": SimpleUploadedFile("h.png", _image("PNG"), "image/png"),
        },
    )
    assert response.status_code == 200, response.content
    identity.refresh_from_db()
    # stored names follow the re-encoded content, which sets the AP mediaType
    assert identity.icon.name.endswith(".webp")
    assert identity.image.name.endswith(".webp")
    with Image.open(identity.icon) as img:
        assert (img.format, img.size) == ("WEBP", (400, 400))


@pytest.mark.django_db
@pytest.mark.usefixtures("_media_root")
@pytest.mark.parametrize("field", ["avatar", "header"])
def test_update_credentials_rejects_unsupported_image(api_client, identity, field):
    name, summary = identity.name, identity.summary
    response = _patch_multipart(
        api_client,
        {
            "display_name": "Changed",
            "note": "changed",
            field: SimpleUploadedFile("p.jpg", b"not an image", "image/jpeg"),
        },
    )
    assert response.status_code == 400, response.content
    identity.refresh_from_db()
    assert (identity.name, identity.summary) == (name, summary)
    assert not identity.icon and not identity.image


@pytest.mark.django_db
def test_verify_credentials(api_client, identity):
    response = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert response["id"] == str(identity.pk)
    assert response["username"] == identity.username


@pytest.mark.django_db
def test_update_credentials_privacy_reflected_immediately(api_client, identity):
    """The update_credentials response must echo the privacy just written, not a
    stale value cached by ConfigLoadingMiddleware via the config_identity
    cached_property (Config.set_identity invalidates that cache)."""
    response = api_client.patch(
        "/api/v1/accounts/update_credentials",
        data=json.dumps({"source": {"privacy": "private"}}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["source"]["privacy"] == "private"


@pytest.mark.django_db
def test_account_search(api_client, identity):
    response = api_client.get("/api/v1/accounts/search?q=test").json()
    assert response[0]["id"] == str(identity.pk)
    assert response[0]["username"] == identity.username


@pytest.mark.django_db
def test_following_count_consistent_after_unfollow(
    api_client, identity, other_identity
):
    """#1616: Account API following_count must reflect follow state changes."""
    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/follow",
        content_type="application/json",
    )
    assert response.status_code == 200
    own = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert own["following_count"] == 1

    response = api_client.post(
        f"/api/v1/accounts/{other_identity.pk}/unfollow",
        content_type="application/json",
    )
    assert response.status_code == 200
    own = api_client.get("/api/v1/accounts/verify_credentials").json()
    assert own["following_count"] == 0


@pytest.mark.django_db
def test_account_action_on_a_merged_id_reaches_the_real_identity(
    api_client, identity, remote_identity
):
    """
    A client keeps the numeric id it was given, which may be one a merge has
    since emptied. Blocking that row would report success while the actor
    went on being seen.
    """
    from users.models import Block, Identity

    alias = Identity.objects.create(
        actor_uri="https://remote.test/users/test/",
        local=False,
        canonical=remote_identity,
    )

    response = api_client.post(
        f"/api/v1/accounts/{alias.pk}/block",
        content_type="application/json",
    )

    assert response.status_code == 200
    assert Block.objects.filter(
        source=identity, target=remote_identity, mute=False
    ).exists()
    assert not Block.objects.filter(target=alias).exists()
    # The client files the answer under the id it sent, so that is the id
    # it gets back, here and when it asks again
    assert response.json()["id"] == str(alias.pk)
    response = api_client.get(f"/api/v1/accounts/relationships?id={alias.pk}")
    assert response.status_code == 200
    assert [(r["id"], r["blocking"]) for r in response.json()] == [
        (str(alias.pk), True)
    ]


@pytest.mark.django_db
def test_a_merged_id_cannot_reach_a_blocked_identity(
    api_client, identity, remote_identity
):
    """
    The moderation check belongs to the identity a request ends at. An alias
    is a row nobody moderated, so checking it and then resolving would serve
    the account behind it however it was restricted.
    """
    from users.models import Identity

    remote_identity.restriction = Identity.Restriction.blocked
    remote_identity.save()
    alias = Identity.objects.create(
        actor_uri="https://remote.test/users/test/",
        local=False,
        canonical=remote_identity,
    )

    assert api_client.get(f"/api/v1/accounts/{alias.pk}").status_code == 404
    assert (
        api_client.post(
            f"/api/v1/accounts/{alias.pk}/follow", content_type="application/json"
        ).status_code
        == 404
    )
