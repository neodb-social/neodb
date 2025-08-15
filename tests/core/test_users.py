import pytest
from django.conf import settings

from mastodon.models import MastodonAccount
from takahe.utils import Takahe
from users.models import APIdentity, User


@pytest.mark.django_db(databases="__all__")
class TestUser:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.alice = User.register(username="alice").identity
        MastodonAccount.objects.create(
            handle="Alice@MySpace", user=self.alice.user, domain="MySpace", uid="42"
        )
        self.bob = User.register(username="bob").identity
        self.domain = settings.SITE_INFO.get("site_domain")

    def test_handle(self):
        assert APIdentity.get_by_handle("Alice") == self.alice
        assert APIdentity.get_by_handle("@alice") == self.alice
        assert APIdentity.get_by_handle("Alice@MySpace", True) == self.alice
        assert APIdentity.get_by_handle("alice@myspace", True) == self.alice
        assert APIdentity.get_by_handle("@alice@" + self.domain) == self.alice
        assert APIdentity.get_by_handle("@Alice@" + self.domain) == self.alice
        with pytest.raises(APIdentity.DoesNotExist):
            APIdentity.get_by_handle("@Alice@MySpace")
        with pytest.raises(APIdentity.DoesNotExist):
            APIdentity.get_by_handle("@alice@KKCity")

    def test_fetch(self):
        pass

    def test_follow(self):
        self.alice.follow(self.bob)
        Takahe._force_state_cycle()
        assert self.alice.is_following(self.bob)
        assert self.bob.is_followed_by(self.alice)
        assert self.alice.following == [self.bob.pk]
        assert self.bob.followers == [self.alice.pk]

        self.alice.unfollow(self.bob)
        Takahe._force_state_cycle()
        assert not self.alice.is_following(self.bob)
        assert not self.bob.is_followed_by(self.alice)
        assert self.alice.following == []
        assert self.bob.followers == []

    def test_mute(self):
        self.alice.mute(self.bob)
        Takahe._force_state_cycle()
        assert self.alice.is_muting(self.bob)
        assert self.alice.ignoring == [self.bob.pk]
        assert self.alice.rejecting == []

    def test_block(self):
        self.alice.block(self.bob)
        Takahe._force_state_cycle()
        assert self.alice.is_blocking(self.bob)
        assert self.bob.is_blocked_by(self.alice)
        assert self.alice.rejecting == [self.bob.pk]
        assert self.alice.ignoring == [self.bob.pk]

        self.alice.unblock(self.bob)
        Takahe._force_state_cycle()
        assert not self.alice.is_blocking(self.bob)
        assert not self.bob.is_blocked_by(self.alice)
        assert self.alice.rejecting == []
        assert self.alice.ignoring == []
