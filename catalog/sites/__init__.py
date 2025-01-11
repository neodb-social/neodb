from ..common.sites import SiteManager
from .ao3 import ArchiveOfOurOwn
from .apple_music import AppleMusic
from .bandcamp import Bandcamp
from .bangumi import Bangumi
from .bgg import BoardGameGeek
from .bookstw import BooksTW
from .discogs import DiscogsMaster, DiscogsRelease
from .douban_book import DoubanBook
from .douban_drama import DoubanDrama
from .douban_game import DoubanGame
from .douban_movie import DoubanMovie
from .douban_music import DoubanMusic
from .fedi import FediverseInstance
from .goodreads import Goodreads
from .google_books import GoogleBooks
from .igdb import IGDB
from .imdb import IMDB
from .jjwxc import JJWXC
from .qidian import Qidian
from .rss import RSS
from .spotify import Spotify
from .steam import Steam
from .tmdb import TMDB_Movie
from .ypshuo import Ypshuo

# from .apple_podcast import ApplePodcast

__all__ = [
    "SiteManager",
    "ArchiveOfOurOwn",
    "AppleMusic",
    "Bandcamp",
    "Bangumi",
    "BoardGameGeek",
    "BooksTW",
    "DiscogsMaster",
    "DiscogsRelease",
    "DoubanBook",
    "DoubanDrama",
    "DoubanGame",
    "DoubanMovie",
    "DoubanMusic",
    "FediverseInstance",
    "Goodreads",
    "GoogleBooks",
    "IGDB",
    "IMDB",
    "JJWXC",
    "Qidian",
    "RSS",
    "Spotify",
    "Steam",
    "TMDB_Movie",
    "Ypshuo",
    # "ApplePodcast",
]
