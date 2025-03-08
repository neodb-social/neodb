from django.test import TestCase

from catalog.common.models import Item
from catalog.models import Edition, IdType, Movie, TVEpisode, TVSeason, TVShow
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
