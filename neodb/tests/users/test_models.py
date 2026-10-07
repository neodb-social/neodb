from collections.abc import Iterator
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from common.models import SiteConfig
from journal.models import Collection, Mark, ShelfType
from takahe.models import Domain, Identity
from takahe.utils import Takahe
from users.models import APIdentity, User
from users.models.user import UsernameValidator


class TestUsernameValidator:
    def setup_method(self):
        self.v = UsernameValidator()

    def test_accepts_valid_usernames(self):
        for name in ("alice123", "alice_bob", "ab", "a" * 30):
            self.v(name)

    def test_reserved_admin_raises(self):
        with pytest.raises(ValidationError):
            self.v("admin")

    def test_reserved_api_raises(self):
        with pytest.raises(ValidationError):
            self.v("api")

    def test_reserved_user_raises(self):
        with pytest.raises(ValidationError):
            self.v("user")

    def test_reserved_case_insensitive(self):
        with pytest.raises(ValidationError):
            self.v("Admin")
        with pytest.raises(ValidationError):
            self.v("API")

    def test_too_short_raises(self):
        with pytest.raises(ValidationError):
            self.v("a")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            self.v("a" * 31)

    def test_hyphen_raises(self):
        with pytest.raises(ValidationError):
            self.v("has-dash")

    def test_space_raises(self):
        with pytest.raises(ValidationError):
            self.v("has space")

    def test_dot_raises(self):
        with pytest.raises(ValidationError):
            self.v("has.dot")


class TestUserMacrolanguage:
    def test_simple_language_code(self):
        u = User(language="en")
        assert u.macrolanguage == "en"

    def test_language_with_region(self):
        u = User(language="zh-Hant")
        assert u.macrolanguage == "zh"

    def test_language_with_script_and_region(self):
        u = User(language="zh-Hans-CN")
        assert u.macrolanguage == "zh"

    def test_empty_language(self):
        u = User(language="")
        assert u.macrolanguage == ""


@pytest.mark.django_db(databases="__all__")
class TestUserModel:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="alice")
        self.superuser = User.register(username="superalice", is_superuser=True)
        self.staff = User.register(username="staffalice", is_staff=True)

    def test_str_contains_username(self):
        assert "alice" in str(self.user)

    def test_get_roles_regular_user(self):
        assert self.user.get_roles() == []

    def test_get_roles_superuser_includes_admin(self):
        assert "admin" in self.superuser.get_roles()

    def test_get_roles_staff_includes_staff(self):
        assert "staff" in self.staff.get_roles()

    def test_register_creates_preference_and_local_identity(self):
        pref = self.user.preference
        assert pref is not None
        assert pref.user == self.user
        assert self.user.identity is not None
        assert self.user.identity.username == "alice"
        assert self.user.identity.local is True

    def test_clear_deactivates_user(self):
        self.user.clear()
        self.user.refresh_from_db()
        assert self.user.is_active is False

    def test_clear_saves_username_to_last_name(self):
        self.user.clear()
        self.user.refresh_from_db()
        assert self.user.last_name == "alice"

    def test_register_duplicate_username_raises(self):
        with pytest.raises(ValidationError):
            User.register(username="alice")

    def test_register_no_username_raises(self):
        with pytest.raises(ValueError, match="username is not set"):
            User.register(username="")

    def test_fresh_user_attributes(self):
        assert "alice" in self.user.url
        assert urlparse(self.user.absolute_url).hostname == "example.org"
        assert self.user.mastodon_acct == ""
        assert self.user.email_account is None
        assert self.user.last_usage is None

    def test_last_usage_returns_time_when_marked(self):
        book = Edition.objects.create(title="Test Book")
        Mark(self.user.identity, book).update(ShelfType.WISHLIST)
        assert self.user.last_usage is not None


@pytest.mark.django_db(databases="__all__")
class TestAPIdentityModel:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="iduser")
        self.identity = self.user.identity

    def test_local_identity_attributes(self):
        assert "iduser" in str(self.identity)
        assert self.identity.handle == "iduser"
        assert "@" in self.identity.full_handle
        assert "iduser" in self.identity.full_handle
        assert "/users/" in self.identity.url
        assert self.identity.is_active is True
        assert self.identity.is_person is True
        assert self.identity.is_bot is False
        assert self.identity.is_group is False

    def test_is_rejecting_self_is_false(self):
        # An identity never rejects itself
        assert self.identity.is_rejecting(self.identity) is False

    def test_get_by_handle_nonexistent_raises(self):
        with pytest.raises(APIdentity.DoesNotExist):
            APIdentity.get_by_handle("nonexistent")

    def test_get_by_handle_invalid_format_raises(self):
        with pytest.raises(APIdentity.DoesNotExist):
            APIdentity.get_by_handle("a@b@c@d")

    def test_identity_clear(self):
        self.identity.clear()
        self.identity.refresh_from_db()
        assert self.identity.deleted is not None


@pytest.mark.django_db(databases="__all__")
class TestRemoteAPIdentity:
    """Remote identities from implementations (e.g. Lemmy) that omit the
    web profile url and/or an avatar must still render sensible values."""

    def _make_remote(self, *, profile_uri=None, icon_uri="", actor_type="group"):
        from django.conf import settings
        from takahe.models import Domain, Identity
        from takahe.utils import Takahe

        self.settings = settings
        domain, _ = Domain.objects.get_or_create(
            domain="lemmy.example", defaults={"local": False}
        )
        identity = Identity.objects.create(
            actor_uri="https://lemmy.example/c/books",
            local=False,
            username="books",
            domain=domain,
            actor_type=actor_type,
            profile_uri=profile_uri,
            icon_uri=icon_uri,
        )
        return Takahe.get_or_create_remote_apidentity(identity)

    def test_profile_uri_falls_back_to_actor_uri(self):
        identity = self._make_remote(profile_uri=None)
        assert identity.profile_uri == "https://lemmy.example/c/books"

    def test_profile_uri_uses_url_when_present(self):
        identity = self._make_remote(profile_uri="https://lemmy.example/c/books/view")
        assert identity.profile_uri == "https://lemmy.example/c/books/view"

    def test_avatar_falls_back_to_default_when_no_icon(self):
        identity = self._make_remote(icon_uri="")
        assert identity.avatar == SiteConfig.system.user_icon

    def test_avatar_uses_proxy_when_icon_present(self):
        identity = self._make_remote(
            icon_uri="https://lemmy.example/pictrs/image/books.png"
        )
        assert identity.avatar == f"/proxy/identity_icon/{identity.pk}/"

    def test_owner_whose_takahe_row_is_gone_hides_its_pieces(self):
        """takahe deletes the row of a remote actor that answers 410 before
        neodb clears the APIdentity; its pieces must not 500 in between."""
        owner = self._make_remote(actor_type="person")
        collection = Collection.objects.create(
            owner=owner, title="Orphaned", brief="", visibility=0, local=False
        )
        Identity.objects.filter(pk=owner.pk).delete()
        owner = APIdentity.objects.get(pk=owner.pk)
        viewer = User.register(username="viewer")

        assert owner.is_active is False
        assert collection.is_visible_to(viewer) is False
        client = Client()
        assert client.get(collection.url).status_code == 403
        client.force_login(viewer, backend="mastodon.auth.OAuth2Backend")
        assert client.get(collection.url).status_code == 403

    def test_cleared_remote_owner_is_inactive(self):
        owner = self._make_remote(actor_type="person")
        owner.deleted = timezone.now()
        owner.save()

        assert owner.is_active is False


@pytest.mark.django_db(databases="__all__")
class TestIdentityMastodonJson:
    """``avatar`` in the api must be absolute.

    ``local_icon_url()`` is also a template src and stays a path there, so the
    serializer resolves it.
    """

    def test_remote_proxy_icon_is_absolute(self):
        domain, _ = Domain.objects.get_or_create(
            domain="remote.example", defaults={"local": False}
        )
        identity = Identity.objects.create(
            actor_uri="https://remote.example/u/bob",
            local=False,
            username="bob",
            domain=domain,
            icon_uri="https://remote.example/avatar.png",
        )
        value = identity.to_mastodon_json()
        proxy = f"https://{settings.SITE_DOMAIN}/proxy/identity_icon/{identity.pk}/"
        assert value["avatar"] == proxy
        assert value["avatar_static"] == proxy

    def test_local_icon_on_schemeless_storage_is_absolute(self):
        user = User.register(email="icon@test.com", username="iconuser")
        identity = user.identity.takahe_identity
        # read before the name is set, which keeps the attribute a file
        storage = identity.icon.storage
        identity.icon = "profile_images/a.png"
        with mock.patch.object(storage, "base_url", "/media/"):
            value = identity.to_mastodon_json()
        expected = f"https://{settings.SITE_DOMAIN}/media/profile_images/a.png"
        assert value["avatar"] == expected
        assert value["avatar_static"] == expected


class TestWebfingerXRD:
    """
    A cache that ignores Vary: Accept can answer a JSON webfinger request with
    the XRD variant of the same resource.
    """

    def test_xrd_answer_is_parsed(self):
        data = Identity.parse_webfinger_xrd(
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<XRD xmlns="http://docs.oasis-open.org/ns/xri/xrd-1.0">'
            b"<Subject>acct:test@remote.example</Subject>"
            b'<Link rel="self" type="application/activity+json"'
            b' href="https://remote.example/users/9u8410yv8ddh0gfg"/>'
            b'<Link rel="http://webfinger.net/rel/profile-page" type="text/html"'
            b' href="https://remote.example/@test"/>'
            b"</XRD>"
        )
        assert data == {
            "subject": "acct:test@remote.example",
            "links": [
                {
                    "rel": "self",
                    "type": "application/activity+json",
                    "href": "https://remote.example/users/9u8410yv8ddh0gfg",
                },
                {
                    "rel": "http://webfinger.net/rel/profile-page",
                    "type": "text/html",
                    "href": "https://remote.example/@test",
                },
            ],
        }

    def test_other_documents_are_rejected(self):
        assert (
            Identity.parse_webfinger_xrd(
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<XRD xmlns="http://docs.oasis-open.org/ns/xri/xrd-1.0">'
                b'<Link rel="self" href="https://remote.example/users/1"/>'
                b"</XRD>"
            )
            is None
        )
        assert (
            Identity.parse_webfinger_xrd(
                b"<!DOCTYPE html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
            )
            is None
        )
        assert Identity.parse_webfinger_xrd(b"") is None


@pytest.mark.django_db(databases="__all__")
class TestAccountMigration:
    @pytest.fixture(autouse=True)
    def setup_migration(self, client: Client) -> Iterator[None]:
        self.user = User.register(username="moveuser")
        self.identity = self.user.identity.takahe_identity
        domain = Domain.objects.create(domain="move.example", local=False)
        self.target = Identity.objects.create(
            actor_uri="https://move.example/users/target",
            username="target",
            domain=domain,
            local=False,
            aliases=[],
        )
        client.force_login(self.user)
        with (
            mock.patch("httpx.get") as self.refresh,
            mock.patch("takahe.utils.is_valid_url", return_value=True),
            mock.patch.object(Identity, "fetch_webfinger", return_value=(None, None)),
            mock.patch.object(Takahe, "fetch_remote_identity") as self.fetch,
        ):
            self.refresh.return_value.status_code = 200
            self.refresh.return_value.json.return_value = {
                "alsoKnownAs": [self.identity.actor_uri]
            }
            yield

    @pytest.mark.parametrize("view", ["migrate_in", "migrate_out"])
    def test_anonymous_requests_cannot_change_identity(
        self, client: Client, view: str
    ) -> None:
        client.logout()
        response = client.post(reverse(f"users:{view}"), {"alias": self.target.handle})
        assert response.status_code == 302
        assert reverse("users:login") in response["Location"]
        self.identity.refresh_from_db()
        assert not self.identity.aliases
        assert self.identity.state not in ("moved", "moved_fanned_out")
        self.refresh.assert_not_called()

    @pytest.mark.parametrize("view", ["migrate_in", "migrate_out"])
    def test_pages_show_existing_aliases(self, client: Client, view: str) -> None:
        self.identity.aliases = [self.target.actor_uri]
        self.identity.save(update_fields=["aliases"])
        response = client.get(reverse(f"users:{view}"))
        assert response.status_code == 200
        assert response.context["aliases"] == [self.target]
        assert response.context["moved"] is False

    @pytest.mark.parametrize("view", ["migrate_in", "migrate_out"])
    def test_empty_handle_does_not_start_lookup(
        self, client: Client, view: str
    ) -> None:
        response = client.post(reverse(f"users:{view}"), {"alias": " @ "})
        assert response.status_code == 302
        self.fetch.assert_not_called()
        self.refresh.assert_not_called()
        self.identity.refresh_from_db()
        assert not self.identity.aliases

    @pytest.mark.parametrize("view", ["migrate_in", "migrate_out"])
    def test_unresolved_handle_queues_lookup(self, client: Client, view: str) -> None:
        response = client.post(
            reverse(f"users:{view}"), {"alias": " @unknown@move.example "}
        )
        assert response.status_code == 302
        self.fetch.assert_called_once_with("unknown@move.example")
        self.identity.refresh_from_db()
        assert not self.identity.aliases
        assert self.identity.state not in ("moved", "moved_fanned_out")

    def test_alias_add_is_idempotent_and_remove_persists(self, client: Client) -> None:
        url = reverse("users:migrate_in")
        for _ in range(2):
            response = client.post(url, {"alias": f" @{self.target.handle} "})
            assert response.status_code == 302
        self.identity.refresh_from_db()
        assert self.identity.aliases == [self.target.actor_uri]
        response = client.post(url, {"alias": self.target.handle, "remove_alias": "1"})
        assert response.status_code == 302
        self.identity.refresh_from_db()
        assert not self.identity.aliases

    @pytest.mark.parametrize("state", ["moved", "moved_fanned_out"])
    @pytest.mark.parametrize("remove", [False, True])
    def test_moved_account_cannot_change_aliases(
        self, client: Client, state: str, remove: bool
    ) -> None:
        self.identity.state = state
        self.identity.aliases = [self.target.actor_uri] if remove else []
        self.identity.save(update_fields=["state", "aliases"])
        data = {"alias": self.target.handle}
        if remove:
            data["remove_alias"] = "1"
        response = client.post(reverse("users:migrate_in"), data)
        assert response.status_code == 302
        self.identity.refresh_from_db()
        assert self.identity.aliases == ([self.target.actor_uri] if remove else [])
        assert self.identity.state == state
        self.fetch.assert_not_called()

    def test_cannot_move_to_local_account(self, client: Client) -> None:
        local_user = User.register(username="localtarget")
        response = client.post(
            reverse("users:migrate_out"), {"alias": local_user.identity.full_handle}
        )
        assert response.status_code == 302
        self.refresh.assert_not_called()
        self.identity.refresh_from_db()
        assert self.identity.state not in ("moved", "moved_fanned_out")
        assert not self.identity.aliases

    def test_move_requires_alias_in_refreshed_target(self, client: Client) -> None:
        self.target.aliases = [self.identity.actor_uri]
        self.target.save(update_fields=["aliases"])
        self.refresh.return_value.json.return_value = {"alsoKnownAs": []}
        response = client.post(
            reverse("users:migrate_out"), {"alias": self.target.handle}
        )
        assert response.status_code == 302
        self.target.refresh_from_db()
        assert self.target.aliases == []
        self.identity.refresh_from_db()
        assert self.identity.state not in ("moved", "moved_fanned_out")
        assert not self.identity.aliases

    def test_verified_move_and_cancel_persist(self, client: Client) -> None:
        response = client.post(
            reverse("users:migrate_out"), {"alias": self.target.handle}
        )
        assert response.status_code == 302
        self.refresh.assert_called_once()
        assert self.refresh.call_args.args == (self.target.actor_uri,)
        self.identity.refresh_from_db()
        assert self.identity.state == "moved"
        assert self.identity.aliases == [self.target.actor_uri]
        page = client.get(reverse("users:migrate_out"))
        assert page.context["moved"] is True
        response = client.post(reverse("users:migrate_out"), {"cancel": "1"})
        assert response.status_code == 302
        self.identity.refresh_from_db()
        assert self.identity.state == "updated"
        assert not self.identity.aliases
        assert self.refresh.call_count == 1
