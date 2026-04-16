import pytest

from catalog.models import Edition
from journal.models import Comment
from journal.models.common import (
    Debris,
    Piece,
    max_visiblity_to_user,
    prefetch_latest_posts,
    q_owned_piece_visible_to_user,
    q_piece_in_home_feed_of_user,
)
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestQOwnedPieceVisibleToUser:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.owner_user = User.register(email="owner@test.com", username="owner")
        self.viewer_user = User.register(email="viewer@test.com", username="viewer")
        self.owner = self.owner_user.identity
        self.viewer = self.viewer_user.identity

    def test_unauthenticated_viewer_anonymous_viewable(self):
        q = q_owned_piece_visible_to_user(None, self.owner)
        assert "visibility" in str(q)

    def test_unauthenticated_viewer_not_anonymous_viewable(self):
        self.owner.anonymous_viewable = False
        self.owner.save()
        q = q_owned_piece_visible_to_user(None, self.owner)
        assert "pk__in" in str(q)

    def test_owner_views_own(self):
        q = q_owned_piece_visible_to_user(self.owner_user, self.owner)
        assert "owner" in str(q)

    def test_non_following_viewer_sees_public_only(self):
        q = q_owned_piece_visible_to_user(self.viewer_user, self.owner)
        assert "visibility" in str(q)

    def test_following_viewer_sees_follower_content(self):
        self.viewer.follow(self.owner, force_accept=True)
        Takahe._force_state_cycle()
        q = q_owned_piece_visible_to_user(self.viewer_user, self.owner)
        assert "visibility__in" in str(q)


@pytest.mark.django_db(databases="__all__")
class TestMaxVisibilityToUser:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.owner_user = User.register(email="mowner@test.com", username="mowner")
        self.viewer_user = User.register(email="mviewer@test.com", username="mviewer")
        self.owner = self.owner_user.identity
        self.viewer = self.viewer_user.identity

    def test_unauthenticated_returns_0(self):
        from unittest.mock import MagicMock

        anon = MagicMock(spec=User)
        anon.is_authenticated = False
        assert max_visiblity_to_user(anon, self.owner) == 0

    def test_owner_returns_2(self):
        assert max_visiblity_to_user(self.owner_user, self.owner) == 2

    def test_non_follower_returns_0(self):
        assert max_visiblity_to_user(self.viewer_user, self.owner) == 0

    def test_follower_returns_1(self):
        self.viewer.follow(self.owner, force_accept=True)
        Takahe._force_state_cycle()
        assert max_visiblity_to_user(self.viewer_user, self.owner) == 1


@pytest.mark.django_db(databases="__all__")
class TestPieceGetByUrl:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="purl@test.com", username="purluser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="URL Book")

    def test_get_by_url_valid(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        found = Comment.get_by_url(comment.uuid)
        assert found is not None
        assert found.pk == comment.pk

    def test_get_by_url_full_url(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        url = comment.url
        found = Comment.get_by_url(url)
        assert found is not None
        assert found.pk == comment.pk

    def test_get_by_url_invalid(self):
        result = Comment.get_by_url("invalid_url")
        assert result is None

    def test_get_by_url_and_owner(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        found = Comment.get_by_url_and_owner(comment.uuid, self.identity.pk)
        assert found is not None
        assert found.pk == comment.pk

    def test_get_by_url_and_owner_wrong_owner(self):
        other_user = User.register(email="other@test.com", username="other")
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        result = Comment.get_by_url_and_owner(comment.uuid, other_user.identity.pk)
        assert result is None

    def test_get_by_url_and_owner_invalid_url(self):
        result = Comment.get_by_url_and_owner("garbage", self.identity.pk)
        assert result is None


@pytest.mark.django_db(databases="__all__")
class TestPieceProperties:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="pprop@test.com", username="ppropuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Prop Book")

    def test_piece_url_and_uuid(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert comment.uuid is not None
        assert len(comment.uuid) in [21, 22]
        assert comment.url.endswith(comment.uuid)

    def test_piece_absolute_url(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert "example.org" in comment.absolute_url
        assert comment.uuid in comment.absolute_url

    def test_piece_like_count_no_post(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert comment.like_count == 0

    def test_piece_reply_count_no_post(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert comment.reply_count == 0

    def test_piece_is_liked_by_no_post(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert not comment.is_liked_by(self.identity)

    def test_piece_classname(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        assert comment.classname == "comment"

    def test_debris_to_indexable_doc(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        debris = Debris.create_from_piece(comment)
        assert debris.to_indexable_doc() == {}

    def test_content_str(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        s = str(comment)
        assert "Comment" in s
        assert comment.uuid in s

    def test_get_by_post_id_none(self):
        result = Piece.get_by_post_id(99999)
        assert result is None


@pytest.mark.django_db(databases="__all__")
class TestPrefetchLatestPosts:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="plp@test.com", username="plpuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="PLP Book")

    def test_prefetch_empty_list(self):
        prefetch_latest_posts([])

    def test_prefetch_pieces_without_posts(self):
        comment = Comment.objects.create(
            owner=self.identity, item=self.book, text="test", visibility=0
        )
        prefetch_latest_posts([comment])
        assert comment.__dict__["latest_post_id"] is None
        assert comment.__dict__["latest_post"] is None


@pytest.mark.django_db(databases="__all__")
class TestQPieceInHomeFeed:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="feed@test.com", username="feeduser")

    def test_home_feed_query(self):
        q = q_piece_in_home_feed_of_user(self.user)
        q_str = str(q)
        assert "owner_id" in q_str
