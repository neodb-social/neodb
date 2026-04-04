import pytest

from catalog.models import Edition
from journal.models import Collection, Mark, ShelfMember, ShelfType
from journal.models.common import q_piece_visible_to_user
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import User


def _set_restriction(identity, restriction: TakaheIdentity.Restriction):
    TakaheIdentity.objects.filter(pk=identity.pk).update(restriction=restriction)
    # clear cached property so subsequent reads reflect the change
    if "takahe_identity" in identity.__dict__:
        del identity.__dict__["takahe_identity"]


@pytest.mark.django_db(databases="__all__")
class TestQPieceVisibleToUser:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.alice = User.register(username="alice_vis")
        self.bob = User.register(username="bob_vis")
        self.carol = User.register(username="carol_vis")
        # alice marks the book publicly
        Mark(self.alice.identity, self.book).update(ShelfType.WISHLIST, visibility=0)

    def _alice_marks(self, viewer_user):
        q = q_piece_visible_to_user(viewer_user)
        return ShelfMember.objects.filter(item=self.book).filter(q)

    def test_unrestricted_visible_to_all(self):
        assert self._alice_marks(self.bob).exists()
        assert self._alice_marks(None).exists()

    def test_restricted_blocked_hidden_from_non_follower(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.blocked)
        assert not self._alice_marks(self.bob).exists()
        assert not self._alice_marks(None).exists()

    def test_restricted_limited_hidden_from_non_follower(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.limited)
        assert not self._alice_marks(self.bob).exists()
        assert not self._alice_marks(None).exists()

    def test_restricted_visible_to_follower(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.limited)
        self.bob.identity.follow(self.alice.identity, force_accept=True)
        Takahe._force_state_cycle()
        assert self._alice_marks(self.bob).exists()

    def test_restricted_always_visible_to_self(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.blocked)
        assert self._alice_marks(self.alice).exists()

    def test_user_block_hides_content(self):
        self.bob.identity.block(self.alice.identity)
        Takahe._force_state_cycle()
        assert not self._alice_marks(self.bob).exists()


@pytest.mark.django_db(databases="__all__")
class TestIsVisibleTo:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.alice = User.register(username="alice_isvt")
        self.bob = User.register(username="bob_isvt")
        self.collection = Collection.objects.create(
            owner=self.alice.identity, title="Alice's Collection", visibility=0
        )

    def test_unrestricted_visible_to_all(self):
        assert self.collection.is_visible_to(self.bob)
        assert self.collection.is_visible_to(None)

    def test_blocked_hidden_from_non_follower(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.blocked)
        assert not self.collection.is_visible_to(self.bob)
        assert not self.collection.is_visible_to(None)

    def test_blocked_visible_to_follower(self):
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.blocked)
        self.bob.identity.follow(self.alice.identity, force_accept=True)
        Takahe._force_state_cycle()
        assert self.collection.is_visible_to(self.bob)

    def test_limited_visible_via_direct_link(self):
        # limited accounts can still be accessed via direct link
        _set_restriction(self.alice.identity, TakaheIdentity.Restriction.limited)
        assert self.collection.is_visible_to(self.bob)

    def test_user_block_hides_collection(self):
        self.bob.identity.block(self.alice.identity)
        Takahe._force_state_cycle()
        assert not self.collection.is_visible_to(self.bob)
