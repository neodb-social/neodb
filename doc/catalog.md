Catalog
=======

Data Models
-----------
all types of catalog items inherits from `Item` which stores as multi-table django model.
one `Item` may have multiple `ExternalResource`s, each represents one page on an external site

```mermaid
classDiagram
    class Item {
        <<abstract>>
    }
    Item <|-- Album
    class Album {
        +String barcode
        +String Douban_ID
        +String Spotify_ID
    }
    Item <|-- Game
    class Game {
        +String Steam_ID
    }
    Item <|-- Podcast
    class Podcast {
        +String feed_url
        +String Apple_ID
    }
    Item <|-- Performance
    Item <|-- Work
    class Work {
        +String Douban_Work_ID
        +String Goodreads_Work_ID
    }
    Item <|-- Edition
    Item <|-- Series

    Series *-- Work
    Work *-- Edition

    class Series {
        +String Goodreads_Series_ID
    }
    class Work {
        +String Douban_ID
        +String Goodreads_ID
    }
    class Edition{
        +String ISBN
        +String Douban_ID
        +String Goodreads_ID
        +String GoogleBooks_ID
    }

    Item <|-- Movie
    Item <|-- TVShow
    Item <|-- TVSeason
    Item <|-- TVEpisode
    TVShow *-- TVSeason
    TVSeason *-- TVEpisode

    class TVShow{
        +String IMDB_ID
        +String TMDB_ID
    }
    class TVSeason{
        +String Douban_ID
        +String TMDB_ID
    }
    class TVEpisode{
        +String IMDB_ID
        +String TMDB_ID
    }
    class Movie{
        +String Douban_ID
        +String IMDB_ID
        +String TMDB_ID
    }

    Item <|-- Collection

    ExternalResource --* Item
    class ExternalResource {
        +enum site
        +url: string
    }
```

Add a new site
--------------
 - add a new value to `IdType` and `SiteName` in `catalog/common/models.py`
 - add a new file in `catalog/sites/`, a new class inherits `AbstractSite`, with:
    * `SITE_NAME`
    * `ID_TYPE`
    * `URL_PATTERNS`
    * `WIKI_PROPERTY_ID` (not used now)
    * `DEFAULT_MODEL` (unless specified in `scrape()` return val)
    * a classmethod `id_to_url()`
    * a method `scrape()` returns a `ResourceContent` object
    * ...

    see existing files in `catalog/sites/` for more examples
 - add an import in `catalog/sites/__init__.py`
 - add some tests
     + add `DOWNLOADER_SAVEDIR = '/tmp'` to settings can save all response to /tmp
     + move captured response file to `test_data/`, except large/images files. Or if have to, use a smallest version (e.g. 1x1 pixel / 1s audio)
     + add `@use_local_response` decorator to test methods that should pick up these responses
 - run all the tests and make sure they pass
 - add `&.source-label__sitename` to `common/static/sass/_Label.sass`
