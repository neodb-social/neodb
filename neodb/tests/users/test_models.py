from unittest import mock
from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError

from catalog.models import Edition
from common.models import SiteConfig
from journal.models import Mark, ShelfType
from takahe.models import Domain, Identity
from users.models import APIdentity, User
from users.models.user import UsernameValidator


class TestUsernameValidator:
    def setup_method(self):
        self.v = UsernameValidator()

    def test_valid_alphanumeric(self):
        self.v("alice123")

    def test_valid_with_underscore(self):
        self.v("alice_bob")

    def test_minimum_length(self):
        self.v("ab")

    def test_maximum_length(self):
        self.v("a" * 30)

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

    def test_url_contains_username(self):
        assert "alice" in self.user.url

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

    def test_mastodon_acct_empty_when_no_mastodon(self):
        assert self.user.mastodon_acct == ""

    def test_email_account_none_when_no_email(self):
        assert self.user.email_account is None

    def test_last_usage_none_when_no_marks(self):
        assert self.user.last_usage is None

    def test_last_usage_returns_time_when_marked(self):
        book = Edition.objects.create(title="Test Book")
        Mark(self.user.identity, book).update(ShelfType.WISHLIST)
        assert self.user.last_usage is not None

    def test_absolute_url_contains_domain(self):
        assert urlparse(self.user.absolute_url).hostname == "example.org"


@pytest.mark.django_db(databases="__all__")
class TestAPIdentityModel:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(username="iduser")
        self.identity = self.user.identity

    def test_str_contains_username(self):
        assert "iduser" in str(self.identity)

    def test_local_handle_is_username(self):
        assert self.identity.handle == "iduser"

    def test_full_handle_contains_at_and_username(self):
        full = self.identity.full_handle
        assert "@" in full
        assert "iduser" in full

    def test_url_contains_users(self):
        assert "/users/" in self.identity.url

    def test_is_active(self):
        assert self.identity.is_active is True

    def test_is_not_bot(self):
        assert self.identity.is_bot is False

    def test_is_not_group(self):
        assert self.identity.is_group is False

    def test_is_rejecting_self_is_false(self):
        # An identity never rejects itself
        assert self.identity.is_rejecting(self.identity) is False

    def test_get_by_handle_local(self):
        found = APIdentity.get_by_handle("iduser")
        assert found.pk == self.identity.pk

    def test_get_by_handle_local_with_at(self):
        found = APIdentity.get_by_handle("@iduser")
        assert found.pk == self.identity.pk

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

    def test_is_person(self):
        assert self.identity.is_person is True


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
