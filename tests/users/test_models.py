import pytest
from django.core.exceptions import ValidationError

from users.models import User
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

    def test_preference_created_on_register(self):
        pref = self.user.preference
        assert pref is not None
        assert pref.user == self.user

    def test_preference_default_visibility(self):
        assert self.user.preference.default_visibility == 0

    def test_preference_default_post_public_mode(self):
        assert self.user.preference.post_public_mode == 0

    def test_preference_mastodon_skip_userinfo_default(self):
        assert self.user.preference.mastodon_skip_userinfo is False

    def test_preference_mastodon_skip_relationship_default(self):
        assert self.user.preference.mastodon_skip_relationship is False

    def test_identity_created_on_register(self):
        assert self.user.identity is not None

    def test_identity_username_matches(self):
        assert self.user.identity.username == "alice"

    def test_identity_is_local(self):
        assert self.user.identity.local is True

    def test_url_contains_username(self):
        assert "alice" in self.user.url

    def test_is_active_by_default(self):
        assert self.user.is_active is True

    def test_clear_deactivates_user(self):
        self.user.clear()
        self.user.refresh_from_db()
        assert self.user.is_active is False


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

    def test_anonymous_viewable_default(self):
        assert self.identity.anonymous_viewable is True

    def test_shelf_manager_available(self):
        assert self.identity.shelf_manager is not None

    def test_tag_manager_available(self):
        assert self.identity.tag_manager is not None
