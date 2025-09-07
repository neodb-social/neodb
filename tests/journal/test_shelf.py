import pytest

from catalog.models import Edition, Game, IdType, ItemCategory, Movie, TVShow
from journal.models.common import q_owned_piece_visible_to_user
from journal.models.review import Review
from journal.models.shelf import ShelfManager, ShelfMember, ShelfType
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestShelfManager:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        # Create a user
        self.user = User.register(email="user@example.com", username="testuser")
        self.identity = self.user.identity

        # Create items of different categories
        self.book = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Test Book"}],
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780553283686",
            author=["Test Author"],
        )

        self.movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Test Movie"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt1234567",
            director=["Test Director"],
            year=2020,
        )

        self.tvshow = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Test Show"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt9876543",
        )

        self.game = Game.objects.create(
            localized_title=[{"lang": "en", "text": "Test Game"}],
            primary_lookup_id_type=IdType.IGDB,
            primary_lookup_id_value="12345",
            developer=["Test Developer"],
        )

        # Initialize shelf manager for user
        self.shelf_manager = ShelfManager(self.identity)

        self._add_items_to_shelves()

    def _add_items_to_shelves(self):
        """Helper to add items to different shelves"""
        # Add books to shelves
        shelves = {
            ShelfType.WISHLIST: [self.book],
            ShelfType.PROGRESS: [
                self.movie,
                self.tvshow,
            ],  # Add same book twice to test count
            ShelfType.COMPLETE: [self.game],
            ShelfType.DROPPED: [],
        }

        # Create shelf members
        for shelf_type, items in shelves.items():
            shelf = self.shelf_manager.get_shelf(shelf_type)
            for item in items:
                ShelfMember.objects.update_or_create(
                    owner=self.identity,
                    item=item,
                    defaults={"visibility": 1, "position": 0, "parent": shelf},
                )

        # Add reviews for some items
        Review.objects.create(
            owner=self.identity,
            item=self.book,
            body="Book review",
            title="Book Review",
            visibility=0,
        )
        Review.objects.create(
            owner=self.identity,
            item=self.movie,
            body="Movie review",
            title="Movie Review",
            visibility=1,
        )
        # Add two reviews for the game to test counts
        Review.objects.create(
            owner=self.identity,
            item=self.game,
            body="Game review 1",
            title="Game Review 1",
            visibility=2,
        )

    def test_get_stats(self):
        """Test that ShelfManager.get_stats() returns correct statistics"""

        # Get stats
        stats = self.shelf_manager.get_stats()

        # Verify structure: stats should have keys for each ItemCategory
        for category in ItemCategory.values:
            assert category in stats

        # Verify each category has counts for each shelf type
        for category in ItemCategory.values:
            for shelf_type in ShelfType.values:
                assert shelf_type in stats[category]
            assert "reviewed" in stats[category]

        # Verify expected counts for book category
        assert stats[ItemCategory.Book][ShelfType.WISHLIST] == 1
        assert stats[ItemCategory.Book][ShelfType.PROGRESS] == 0
        assert stats[ItemCategory.Book][ShelfType.COMPLETE] == 0
        assert stats[ItemCategory.Book][ShelfType.DROPPED] == 0
        assert stats[ItemCategory.Book]["reviewed"] == 1

        # Verify expected counts for movie category
        assert stats[ItemCategory.Movie][ShelfType.WISHLIST] == 0
        assert stats[ItemCategory.Movie][ShelfType.PROGRESS] == 1
        assert stats[ItemCategory.Movie][ShelfType.COMPLETE] == 0
        assert stats[ItemCategory.Movie][ShelfType.DROPPED] == 0
        assert stats[ItemCategory.Movie]["reviewed"] == 1

        # Verify expected counts for TV category
        assert stats[ItemCategory.TV][ShelfType.WISHLIST] == 0
        assert stats[ItemCategory.TV][ShelfType.PROGRESS] == 1
        assert stats[ItemCategory.TV][ShelfType.COMPLETE] == 0
        assert stats[ItemCategory.TV][ShelfType.DROPPED] == 0
        assert stats[ItemCategory.TV]["reviewed"] == 0

        # Verify expected counts for game category
        assert stats[ItemCategory.Game][ShelfType.WISHLIST] == 0
        assert stats[ItemCategory.Game][ShelfType.PROGRESS] == 0
        assert stats[ItemCategory.Game][ShelfType.COMPLETE] == 1
        assert stats[ItemCategory.Game][ShelfType.DROPPED] == 0
        assert stats[ItemCategory.Game]["reviewed"] == 1

    def test_get_stats_with_filter(self):
        """Test ShelfManager.get_stats() with a filter"""

        q1 = q_owned_piece_visible_to_user(None, self.identity)
        stats1 = self.shelf_manager.get_stats(q=q1)
        assert stats1[ItemCategory.Book][ShelfType.WISHLIST] == 0
        assert stats1[ItemCategory.Book]["reviewed"] == 1
        assert stats1[ItemCategory.Movie]["reviewed"] == 0
        assert stats1[ItemCategory.Game]["reviewed"] == 0

        # Create a second user to make sure filtering works
        user2 = User.register(email="user2@example.com", username="testuser2")
        user2.identity.follow(self.user.identity, True)
        q2 = q_owned_piece_visible_to_user(user2, self.identity)
        stats2 = self.shelf_manager.get_stats(q=q2)
        assert stats2[ItemCategory.Book][ShelfType.WISHLIST] == 1
        assert stats2[ItemCategory.Book]["reviewed"] == 1
        assert stats2[ItemCategory.Movie]["reviewed"] == 1
        assert stats2[ItemCategory.Game]["reviewed"] == 0
