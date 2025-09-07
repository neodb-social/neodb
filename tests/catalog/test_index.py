from unittest.mock import MagicMock, patch

import pytest
from django_redis.client import DefaultClient

from catalog.index import (
    CatalogIndex,
    CatalogQueryParser,
    _cat_to_class,
)
from catalog.models import Edition, Item, ItemCategory, Movie


@pytest.mark.django_db(databases="__all__")
class TestCatalogIndex:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        # clean up all data from previous tests. TODO: move this to fixture
        CatalogIndex().delete_all()

        self.book = Edition.objects.create(title="Test Book")
        self.book.isbn = "9781234567890"
        self.book.save()

        self.movie = Movie.objects.create(title="Test Movie")
        self.movie.save()

        # Setup mock for redis connection
        self.redis_patcher = patch("catalog.index.get_redis_connection")
        self.mock_redis = self.redis_patcher.start()
        self.mock_redis.return_value = MagicMock(spec=DefaultClient)

        # Setup mock for the index instance
        self.index_instance_patcher = patch.object(CatalogIndex, "instance")
        self.mock_index_instance = self.index_instance_patcher.start()
        self.mock_index = MagicMock(spec=CatalogIndex)
        self.mock_index_instance.return_value = self.mock_index

        yield

        self.redis_patcher.stop()
        self.index_instance_patcher.stop()

    def test_cat_to_class(self):
        """Test _cat_to_class function converts category string to class names"""
        book_classes = _cat_to_class(ItemCategory.Book.value)
        assert "Edition" in book_classes
        assert "Work" in book_classes

        movie_classes = _cat_to_class(ItemCategory.Movie.value)
        assert "Movie" in movie_classes

    def test_items_to_docs(self):
        """Test the items_to_docs method converts items to indexable documents"""
        items = [self.book, self.movie]

        # Mock to_indexable_doc methods to return predictable results
        with patch.object(Item, "to_indexable_doc") as mock_to_doc:
            mock_to_doc.side_effect = [
                {"id": "1", "item_id": 1, "title": ["Test Book"]},
                {"id": "2", "item_id": 2, "title": ["Test Movie"]},
            ]

            docs = CatalogIndex.items_to_docs(items)

            assert len(docs) == 2
            assert docs[0]["id"] == "1"
            assert docs[1]["id"] == "2"

    def test_replace_items(self):
        """Test the replace_items method updates the index with item data"""
        # Create a real index instance
        index = CatalogIndex()

        # Mock dependencies
        with (
            patch.object(CatalogIndex, "replace_docs") as mock_replace_docs,
            patch.object(CatalogIndex, "delete_docs") as mock_delete_docs,
        ):
            # Test with valid items
            item_ids = [self.book.pk, self.movie.pk]
            index.replace_items(item_ids)

            # Verify replace_docs was called with valid docs
            mock_replace_docs.assert_called_once()

            # Test with deleted or merged items
            self.book.is_deleted = True
            self.book.save()
            self.movie.merged_to_item_id = self.book.pk
            self.movie.save()

            mock_replace_docs.reset_mock()
            index.replace_items(item_ids)

            # Should try to delete these items from index
            mock_delete_docs.assert_called_once()


@pytest.mark.django_db(databases="__all__")
class TestCatalogQueryParser:
    def test_init_basic(self):
        """Test basic initialization of CatalogQueryParser"""
        parser = CatalogQueryParser("test query", 1, 20)

        assert parser.q == "test query"
        assert parser.page == 1
        assert parser.page_size == 20
        assert parser.filter_by == {}

    def test_tag_filtering(self):
        """Test tag filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("tag:scifi,fantasy", 1, 20)

        assert parser.q == ""
        assert sorted(parser.filter_by.get("tag", [])) == sorted(["scifi", "fantasy"])

    def test_category_filtering(self):
        """Test category filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("category:book,movie", 1, 20)

        assert parser.q == ""
        assert "item_class" in parser.filter_by

        # Should include Edition, Work and Movie classes
        classes = parser.filter_by["item_class"]
        assert "Edition" in classes
        assert "Work" in classes
        assert "Movie" in classes

    def test_year_filtering_range(self):
        """Test year range filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("year:2010..2020", 1, 20)

        assert parser.q == ""
        assert parser.filter_by.get("date") == ["20100000..20209999"]

    def test_year_filtering_single(self):
        """Test single year filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("year:2015", 1, 20)

        assert parser.q == ""
        assert parser.filter_by.get("date") == ["20150000..20159999"]

    def test_exclude_categories(self):
        """Test exclude_categories parameter in CatalogQueryParser"""
        parser = CatalogQueryParser("test", 1, 20, exclude_categories=["book", "movie"])

        assert parser.q == "test"
        assert "item_class" in parser.exclude_by

        # Should exclude Edition, Work, and Movie classes
        excluded = parser.exclude_by["item_class"]
        assert "Edition" in excluded
        assert "Work" in excluded
        assert "Movie" in excluded

    def test_filter_categories_precedence(self):
        """Test that filter_categories overrides exclude_categories"""
        parser = CatalogQueryParser(
            "test", 1, 20, filter_categories=["book"], exclude_categories=["movie"]
        )

        assert parser.q == "test"
        assert "item_class" in parser.filter_by
        assert "item_class" not in parser.exclude_by

        # Should include book classes only
        classes = parser.filter_by["item_class"]
        assert "Edition" in classes
        assert "Work" in classes

    def test_to_search_params(self):
        """Test conversion to search parameters"""
        parser = CatalogQueryParser("tag:scifi year:2020..2022", 2, 10)

        params = parser.to_search_params()

        assert params["q"] == ""
        assert params["page"] == 2
        assert params["per_page"] == 10
        assert "filter_by" in params
        assert "tag:scifi" in params["filter_by"]
        assert "date:20200000..20229999" in params["filter_by"]

    def test_type_filtering(self):
        """Test type filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("format:web,dvd", 1, 20)

        assert parser.q == ""
        assert sorted(parser.filter_by.get("format", [])) == sorted(["web", "dvd"])

    def test_genre_filtering(self):
        """Test genre filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("genre:scifi,fantasy,drama", 1, 20)

        assert parser.q == ""
        assert sorted(parser.filter_by.get("genre", [])) == sorted(
            ["scifi", "fantasy", "drama"]
        )

    def test_people_filtering(self):
        """Test people filtering in CatalogQueryParser"""
        parser = CatalogQueryParser("people:spielberg,lucas", 1, 20)

        assert parser.q == ""
        assert sorted(parser.filter_by.get("people", [])) == sorted(
            ["spielberg", "lucas"]
        )

    def test_people_filtering_with_quotes(self):
        """Test people filtering with quoted names in CatalogQueryParser"""
        parser = CatalogQueryParser('people:"Steven Spielberg,George Lucas"', 1, 20)

        assert parser.q == ""
        assert sorted(parser.filter_by.get("people", [])) == sorted(
            ["steven spielberg", "george lucas"]
        )


@pytest.mark.django_db(databases="__all__")
class TestCatalogSearch:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        # clean up all data from previous tests. TODO: move this to fixture
        CatalogIndex().delete_all()

        # Create book test data
        self.book1 = Edition.objects.create(title="The Lord of the Rings")
        self.book1.localized_title = [{"lang": "en", "text": "The Lord of the Rings"}]
        self.book1.isbn = "9780618640157"
        self.book1.author = ["J.R.R. Tolkien"]
        self.book1.pub_year = 1954
        self.book1.pub_house = "Allen & Unwin"
        self.book1.language = ["en"]  # type: ignore
        self.book1.save()

        self.book2 = Edition.objects.create(title="The Hobbit")
        self.book2.localized_title = [{"lang": "en", "text": "The Hobbit"}]
        self.book2.isbn = "9780547928227"
        self.book2.author = ["J.R.R. Tolkien"]
        self.book2.pub_year = 1937
        self.book2.pub_house = "Allen & Unwin"
        self.book2.language = ["en"]  # type: ignore
        self.book2.save()

        self.book3 = Edition.objects.create(title="Dune")
        self.book3.localized_title = [{"lang": "en", "text": "Dune"}]
        self.book3.isbn = "9780441172719"
        self.book3.author = ["Frank Herbert"]
        self.book3.pub_year = 1965
        self.book3.pub_house = "Chilton Books"
        self.book3.language = ["en", "fr"]  # type: ignore
        self.book3.save()

        # Create movie test data
        self.movie1 = Movie.objects.create(title="The Godfather")
        self.movie1.localized_title = [{"lang": "en", "text": "The Godfather"}]
        self.movie1.imdb = "tt0068646"
        self.movie1.director = ["Francis Ford Coppola"]
        self.movie1.actor = ["Marlon Brando", "Al Pacino", "James Caan"]
        self.movie1.year = 1972
        self.movie1.language = ["it"]  # type: ignore
        self.movie1.save()

        self.movie2 = Movie.objects.create(title="The Godfather: Part II")
        self.movie2.localized_title = [{"lang": "en", "text": "The Godfather: Part II"}]
        self.movie2.imdb = "tt0071562"
        self.movie2.director = ["Francis Ford Coppola"]
        self.movie2.actor = ["Al Pacino", "Robert De Niro", "Robert Duvall"]
        self.movie2.year = 1974
        self.movie2.language = ["it", "en"]  # type: ignore
        self.movie2.save()

        self.movie3 = Movie.objects.create(title="Inception")
        self.movie3.localized_title = [{"lang": "en", "text": "Inception"}]
        self.movie3.imdb = "tt1375666"
        self.movie3.director = ["Christopher Nolan"]
        self.movie3.actor = [
            "Marlon Brando",
            "Leonardo DiCaprio",
            "Joseph Gordon-Levitt",
            "Ellen Page",
        ]
        self.movie3.year = 2010
        self.movie3.language = ["en"]  # type: ignore
        self.movie3.save()

        # Index the items for searching
        for item in [
            self.book1,
            self.book2,
            self.book3,
            self.movie1,
            self.movie2,
            self.movie3,
        ]:
            CatalogIndex.instance().replace_item(item)

        yield

        # Clean up the index
        for item in [
            self.book1,
            self.book2,
            self.book3,
            self.movie1,
            self.movie2,
            self.movie3,
        ]:
            CatalogIndex.instance().delete_item(item)

    def test_search_by_author(self):
        """Test searching catalog by author name"""
        # Create query parser for author search
        parser = CatalogQueryParser("Tolkien", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items

    def test_search_by_director(self):
        """Test searching catalog by director name"""
        # Create query parser for director search
        parser = CatalogQueryParser("Coppola", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.movie1.pk in found_items
        assert self.movie2.pk in found_items

    def test_search_by_year(self):
        """Test searching catalog by specific year"""
        # Create query parser for year search
        parser = CatalogQueryParser("year:1974", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.movie2.pk in found_items

    def test_search_by_year_range(self):
        """Test searching catalog by year range"""
        # Create query parser for year range search
        parser = CatalogQueryParser("year:1950..1970", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book3.pk in found_items

    def test_search_by_keyword_and_category(self):
        """Test searching catalog by keyword and category"""
        # Create query parser for keyword and category search
        parser = CatalogQueryParser("Rings category:book", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.book1.pk in found_items

    def test_search_by_keyword_and_year(self):
        """Test searching catalog by keyword and year"""
        # Create query parser for keyword and year search
        parser = CatalogQueryParser("Inception year:2010", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.movie3.pk in found_items

    def test_search_by_actor(self):
        """Test searching catalog by actor name"""
        # Create query parser for actor search
        parser = CatalogQueryParser("Al Pacino", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.movie1.pk in found_items
        assert self.movie2.pk in found_items

    def test_search_complex_query(self):
        """Test searching catalog with complex query combining multiple filters"""
        # Create query parser for complex search
        parser = CatalogQueryParser("Godfather category:movie year:1972", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.movie1.pk in found_items

    def test_multiple_people_search(self):
        """Test searching for items by multiple people (director AND actor)"""
        # Create query parser for people search
        parser = CatalogQueryParser("Coppola Pacino", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.movie1.pk in found_items
        assert self.movie2.pk in found_items

    def test_exclude_category(self):
        """Test searching with excluded categories"""
        # Create query parser with excluded categories
        parser = CatalogQueryParser("Tolkien", 1, 20, exclude_categories=["movie"])

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items

    def test_search_with_pagination(self):
        """Test search results pagination"""
        # Create query parser with pagination
        parser = CatalogQueryParser(
            "", 1, 2
        )  # Empty query to get all items, limited to 2 per page

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify pagination
        assert len(results.items) == 2

        # Get second page
        parser = CatalogQueryParser("", 2, 2)
        results = CatalogIndex.instance().search(parser)

        # Verify second page
        assert len(results.items) == 2

    def test_search_by_people_director(self):
        """Test searching catalog by director name using the people filter"""
        # Create query parser for director search with people filter
        parser = CatalogQueryParser('people:"Francis Ford Coppola"', 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.movie1.pk in found_items
        assert self.movie2.pk in found_items

    def test_search_by_people_actor(self):
        """Test searching catalog by actor name using the people filter"""
        # Create query parser for actor search with people filter
        parser = CatalogQueryParser('people:"Leonardo DiCaprio"', 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.movie3.pk in found_items

    def test_search_by_people_author(self):
        """Test searching catalog by author name using the people filter"""
        # Create query parser for author search with people filter
        parser = CatalogQueryParser('people:"J.R.R. Tolkien"', 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items

    def test_search_by_multiple_people(self):
        """Test searching catalog by multiple people names"""
        parser = CatalogQueryParser('people:"J.R.R. Tolkien,Frank Herbert"', 1, 20)
        results = CatalogIndex.instance().search(parser)
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 3
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items
        assert self.book3.pk in found_items

        # FIXME: more than one people should be AND
        # parser = CatalogQueryParser('people:"Coppola" people:"Marlon Brando"', 1, 20)
        # results = CatalogIndex.instance().search(parser)
        # found_items = [item.pk for item in results.items]
        # assert len(found_items) == 1
        # assert self.movie1.pk in found_items

    def test_search_by_company_publisher(self):
        """Test searching catalog by publishing house name"""
        # Create query parser for publisher search
        parser = CatalogQueryParser("Allen & Unwin", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items

    def test_search_by_company_filter(self):
        """Test searching catalog by publishing house using company filter"""
        # Create query parser for publisher search with company filter
        parser = CatalogQueryParser('company:"Allen & Unwin"', 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items

    def test_search_by_multiple_companies(self):
        """Test searching catalog by multiple company names"""
        # Create query parser for multiple companies search
        parser = CatalogQueryParser('company:"Allen & Unwin,Chilton Books"', 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 3
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items
        assert self.book3.pk in found_items

    def test_search_by_language(self):
        """Test searching catalog by language filter"""
        # Create query parser for language search - English
        parser = CatalogQueryParser("language:en", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 5
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items
        assert self.book3.pk in found_items
        assert self.movie2.pk in found_items
        assert self.movie3.pk in found_items

        # Create query parser for language search - Italian
        parser = CatalogQueryParser("language:it", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 2
        assert self.movie1.pk in found_items
        assert self.movie2.pk in found_items

        # Create query parser for language search - French
        parser = CatalogQueryParser("language:fr", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 1
        assert self.book3.pk in found_items

        # Create query parser for multiple languages search
        parser = CatalogQueryParser("language:en,fr", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify results - should return items with either language
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 5
        assert self.book1.pk in found_items
        assert self.book2.pk in found_items
        assert self.book3.pk in found_items
        assert self.movie2.pk in found_items
        assert self.movie3.pk in found_items

    def test_facet_by_category_includes_all_categories(self):
        """Test facet_by_category returns all categories even with 0 count"""
        from catalog.models import ItemCategory

        # Create query parser for language search - English
        parser = CatalogQueryParser("language:en", 1, 20)

        # Perform search
        results = CatalogIndex.instance().search(parser)

        # Verify search results
        found_items = [item.pk for item in results.items]
        assert len(found_items) == 5

        # Get facet counts by category
        category_facets = results.facet_by_category

        # Verify all categories are present in facets
        for category in ItemCategory:
            assert category.value in category_facets

        # Book and Movie categories should have non-zero counts
        assert category_facets[ItemCategory.Book.value] > 0
        assert category_facets[ItemCategory.Movie.value] > 0

        # Other categories that don't have items in the test data should have zero counts
        assert category_facets[ItemCategory.TV.value] == 0
        assert category_facets[ItemCategory.Music.value] == 0
        assert category_facets[ItemCategory.Game.value] == 0
        assert category_facets[ItemCategory.Podcast.value] == 0
        assert category_facets[ItemCategory.Performance.value] == 0
