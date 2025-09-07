from django.test import TestCase

from catalog.models import (
    Edition,
    Game,
    IdType,
    Item,
    Movie,
    TVEpisode,
    TVSeason,
    TVShow,
)
from journal.models.rating import Rating
from users.models import User


class RatingTest(TestCase):
    databases = "__all__"

    def setUp(self):
        # Create 10 users
        self.users = []
        for i in range(10):
            user = User.register(email=f"user{i}@example.com", username=f"user{i}")
            self.users.append(user)

        # Create a book
        self.book = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Test Book"}],
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780553283686",
            author=["Test Author"],
        )

        # Create a movie
        self.movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Test Movie"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt1234567",
            director=["Test Director"],
            year=2020,
        )

        # Create a game (will have no ratings)
        self.game = Game.objects.create(
            localized_title=[{"lang": "en", "text": "Test Game"}],
            primary_lookup_id_type=IdType.Steam,
            primary_lookup_id_value="12345",
            developer=["Test Developer"],
            platform=["PC"],
            release_year=2022,
        )

        # Create a TV show with a season and episode
        self.tvshow = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Test Show"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt9876543",
        )
        self.tvseason = TVSeason.objects.create(
            localized_title=[{"lang": "en", "text": "Season 1"}],
            show=self.tvshow,
            season_number=1,
        )
        self.tvepisode = TVEpisode.objects.create(
            localized_title=[{"lang": "en", "text": "Episode 1"}],
            season=self.tvseason,
            episode_number=1,
        )

    def test_rating_basic(self):
        """Test basic rating functionality for a single item."""
        # Add ratings for the book from all users
        ratings = [7, 8, 9, 10, 8, 7, 6, 9, 10, 8]

        for i, user in enumerate(self.users):
            Rating.update_item_rating(
                self.book, user.identity, ratings[i], visibility=1
            )

        # Get rating info for the book
        rating_info = Rating.get_info_for_item(self.book)

        # Check rating count
        self.assertEqual(rating_info["count"], 10)

        # Check average rating - should be 8.2
        expected_avg = sum(ratings) / len(ratings)
        self.assertEqual(rating_info["average"], round(expected_avg, 1))

        # Check distribution
        # [1-2, 3-4, 5-6, 7-8, 9-10] buckets represented as percentages
        expected_distribution = [0, 0, 10, 50, 40]  # Based on our ratings
        self.assertEqual(rating_info["distribution"], expected_distribution)

        # Test individual user rating
        user_rating = Rating.get_item_rating(self.book, self.users[0].identity)
        self.assertEqual(user_rating, 7)

        book = Item.objects.get(pk=self.book.pk)
        self.assertEqual(book.rating, round(expected_avg, 1))
        self.assertEqual(book.rating_count, 10)
        self.assertEqual(book.rating_distribution, expected_distribution)

    def test_rating_multiple_items(self):
        """Test ratings across multiple items."""
        # Rate the movie with varying scores
        movie_ratings = [3, 4, 5, 6, 7, 8, 9, 10, 2, 1]

        for i, user in enumerate(self.users):
            Rating.update_item_rating(
                self.movie, user.identity, movie_ratings[i], visibility=1
            )

        # Rate the TV show
        tvshow_ratings = [10, 9, 8, 9, 10, 9, 8, 10, 9, 8]

        for i, user in enumerate(self.users):
            Rating.update_item_rating(
                self.tvshow, user.identity, tvshow_ratings[i], visibility=1
            )

        # Get rating info for both items
        movie_info = Rating.get_info_for_item(self.movie)
        tvshow_info = Rating.get_info_for_item(self.tvshow)

        # Check counts
        self.assertEqual(movie_info["count"], 10)
        self.assertEqual(tvshow_info["count"], 10)

        # Check averages
        expected_movie_avg = sum(movie_ratings) / len(movie_ratings)
        expected_tvshow_avg = sum(tvshow_ratings) / len(tvshow_ratings)

        self.assertEqual(movie_info["average"], round(expected_movie_avg, 1))
        self.assertEqual(tvshow_info["average"], round(expected_tvshow_avg, 1))

        # Check distribution for movie
        # [1-2, 3-4, 5-6, 7-8, 9-10] buckets
        expected_movie_distribution = [
            20,
            20,
            20,
            20,
            20,
        ]  # Evenly distributed across buckets
        self.assertEqual(movie_info["distribution"], expected_movie_distribution)

        # Check distribution for TV show
        # [1-2, 3-4, 5-6, 7-8, 9-10] buckets
        expected_tvshow_distribution = [0, 0, 0, 30, 70]  # High ratings only
        self.assertEqual(tvshow_info["distribution"], expected_tvshow_distribution)

    def test_rating_update_and_delete(self):
        """Test updating and deleting ratings."""
        # Add initial ratings
        for user in self.users[:5]:
            Rating.update_item_rating(self.tvepisode, user.identity, 8, visibility=1)

        # Check initial count
        self.assertEqual(Rating.get_rating_count_for_item(self.tvepisode), 5)

        # Update a rating
        Rating.update_item_rating(
            self.tvepisode, self.users[0].identity, 10, visibility=1
        )

        # Check that rating was updated
        updated_rating = Rating.get_item_rating(self.tvepisode, self.users[0].identity)
        self.assertEqual(updated_rating, 10)

        # Delete a rating by setting it to None
        Rating.update_item_rating(
            self.tvepisode, self.users[1].identity, None, visibility=1
        )

        # Check that rating count decreased
        self.assertEqual(Rating.get_rating_count_for_item(self.tvepisode), 4)

        # Check that the rating was deleted
        deleted_rating = Rating.get_item_rating(self.tvepisode, self.users[1].identity)
        self.assertIsNone(deleted_rating)

    def test_rating_minimum_count(self):
        """Test the minimum rating count threshold."""
        # Add only 4 ratings to the book (below MIN_RATING_COUNT of 5)
        for user in self.users[:4]:
            Rating.update_item_rating(self.book, user.identity, 10, visibility=1)

        # Check that get_rating_for_item returns None due to insufficient ratings
        rating = Rating.get_rating_for_item(self.book)
        self.assertIsNone(rating)

        # Add one more rating to reach the threshold
        Rating.update_item_rating(self.book, self.users[4].identity, 10, visibility=1)

        # Now we should get a valid rating
        rating = Rating.get_rating_for_item(self.book)
        self.assertEqual(rating, 10.0)

    def test_tvshow_rating_includes_children(self):
        """Test that TV show ratings include ratings from child items."""
        # Rate the TV show directly
        Rating.update_item_rating(self.tvshow, self.users[0].identity, 6, visibility=1)

        # Rate the episode (which is a child of the TV show)
        for i in range(1, 6):  # Users 1-5
            Rating.update_item_rating(
                self.tvseason, self.users[i].identity, 10, visibility=1
            )

        # Get info for TV show - should include ratings from episode
        tvshow_info = Rating.get_info_for_item(self.tvshow)

        # Check count (1 for show + 5 for episode = 6)
        self.assertEqual(tvshow_info["count"], 6)

        # The average should consider all ratings (6 + 5*10 = 56, divided by 6 = 9.3)
        self.assertEqual(tvshow_info["average"], 9.3)

    def test_get_info_for_items(self):
        """Test getting rating info for multiple items at once."""
        # Add ratings for the book
        book_ratings = [7, 8, 9, 10, 8]
        for i in range(5):
            Rating.update_item_rating(
                self.book, self.users[i].identity, book_ratings[i], visibility=1
            )

        # Add ratings for the movie
        movie_ratings = [3, 4, 5, 6, 7]
        for i in range(5):
            Rating.update_item_rating(
                self.movie, self.users[i].identity, movie_ratings[i], visibility=1
            )

        # Add ratings for TV show and its season (child item)
        # TV show direct ratings
        tvshow_ratings = [8, 9, 10]
        for i in range(3):
            Rating.update_item_rating(
                self.tvshow, self.users[i].identity, tvshow_ratings[i], visibility=1
            )

        # TV season ratings (should be included in TV show's ratings)
        tvseason_ratings = [7, 8, 9]
        for i in range(3):
            Rating.update_item_rating(
                self.tvseason, self.users[i].identity, tvseason_ratings[i], visibility=1
            )

        # Get ratings for all items at once, including a game with no ratings
        items = [self.book, self.movie, self.tvshow, self.tvseason, self.game]
        ratings_info = Rating.get_info_for_items(items)

        # Check that we got info for all five items
        self.assertEqual(len(ratings_info), 5)
        self.assertIn(self.book.pk, ratings_info)
        self.assertIn(self.movie.pk, ratings_info)
        self.assertIn(self.tvshow.pk, ratings_info)
        self.assertIn(self.tvseason.pk, ratings_info)
        self.assertIn(self.game.pk, ratings_info)

        # Check book ratings
        book_info = ratings_info[self.book.pk]
        self.assertEqual(book_info["count"], 5)
        self.assertEqual(
            book_info["average"], round(sum(book_ratings) / len(book_ratings), 1)
        )

        # Check movie ratings
        movie_info = ratings_info[self.movie.pk]
        self.assertEqual(movie_info["count"], 5)
        self.assertEqual(
            movie_info["average"], round(sum(movie_ratings) / len(movie_ratings), 1)
        )

        # Check TV show ratings - should include both direct and child item ratings
        tvshow_info = ratings_info[self.tvshow.pk]
        # 3 direct ratings + 3 season ratings = 6 total
        self.assertEqual(tvshow_info["count"], 6)

        # Calculate expected average: (8+9+10+7+8+9)/6 = 8.5
        combined_ratings = tvshow_ratings + tvseason_ratings
        expected_avg = round(sum(combined_ratings) / len(combined_ratings), 1)
        self.assertEqual(tvshow_info["average"], expected_avg)

        # Check TV season ratings - should have None for average since count < MIN_RATING_COUNT (5)
        tvseason_info = ratings_info[self.tvseason.pk]
        self.assertEqual(tvseason_info["count"], 3)
        self.assertIsNone(tvseason_info["average"])

        # Check game ratings - should have zero count and None for average (no ratings)
        game_info = ratings_info[self.game.pk]
        self.assertEqual(game_info["count"], 0)
        self.assertIsNone(game_info["average"])
        self.assertEqual(game_info["distribution"], [0, 0, 0, 0, 0])

        # Test with empty list
        self.assertEqual(Rating.get_info_for_items([]), {})

    def test_attach_to_items(self):
        """Test attaching rating_info to a list of items."""
        # Prepare ratings for book and movie
        book_ratings = [5, 6, 7, 8, 9]
        movie_ratings = [1, 2, 3, 4, 5]
        for i, user in enumerate(self.users[:5]):
            Rating.update_item_rating(
                self.book, user.identity, book_ratings[i], visibility=1
            )
            Rating.update_item_rating(
                self.movie, user.identity, movie_ratings[i], visibility=1
            )

        # Prepare items list including one with no ratings
        items = [self.book, self.movie, self.game]
        # Attach ratings to items
        result = Rating.attach_to_items(items)
        # Should return the same list object
        self.assertIs(result, items)

        # Get expected info mapping
        expected_infos = Rating.get_info_for_items(items)
        for item in items:
            # Each item should have a rating_info attribute
            self.assertTrue(hasattr(item, "rating_info"))
            # rating_info should match expected info
            self.assertEqual(item.rating_info, expected_infos.get(item.pk, {}))

        # Test with empty list
        empty_items = []
        result_empty = Rating.attach_to_items(empty_items)
        self.assertIs(result_empty, empty_items)
