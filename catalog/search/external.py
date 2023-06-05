from urllib.parse import quote_plus
from django.conf import settings
from catalog.common import *
from catalog.models import *
from catalog.sites.spotify import get_spotify_token
import requests
from lxml import html
import logging

SEARCH_PAGE_SIZE = 5  # not all apis support page size
logger = logging.getLogger(__name__)


class SearchResultItem:
    def __init__(
        self, category, source_site, source_url, title, subtitle, brief, cover_url
    ):
        self.category = category
        self.external_resources = {
            "all": [{"url": source_url, "site_name": {"label": source_site}}]
        }
        self.source_site = source_site
        self.source_url = source_url
        self.display_title = title
        self.subtitle = subtitle
        self.brief = brief
        self.cover_image_url = cover_url

    @property
    def verbose_category_name(self):
        return self.category.label

    @property
    def url(self):
        return f"/search?q={quote_plus(self.source_url)}"

    @property
    def scraped(self):
        return False


class ProxiedRequest:
    @classmethod
    def get(cls, url):
        u = f"http://api.scraperapi.com?api_key={settings.SCRAPERAPI_KEY}&url={quote_plus(url)}"
        return requests.get(u, timeout=10)


class Goodreads:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            search_url = (
                f"https://www.goodreads.com/search?page={page}&q={quote_plus(q)}"
            )
            r = requests.get(search_url)
            if r.url.startswith("https://www.goodreads.com/book/show/"):
                # Goodreads will 302 if only one result matches ISBN
                res = SiteManager.get_site_by_url(r.url).get_resource_ready()
                subtitle = f"{res.metadata['pub_year']} {', '.join(res.metadata['author'])} {', '.join(res.metadata['translator'] if res.metadata['translator'] else [])}"
                results.append(
                    SearchResultItem(
                        ItemCategory.Book,
                        SiteName.Goodreads,
                        res.url,
                        res.metadata["title"],
                        subtitle,
                        res.metadata["brief"],
                        res.metadata["cover_image_url"],
                    )
                )
            else:
                h = html.fromstring(r.content.decode("utf-8"))
                for c in h.xpath('//tr[@itemtype="http://schema.org/Book"]'):
                    el_cover = c.xpath('.//img[@class="bookCover"]/@src')
                    cover = el_cover[0] if el_cover else None
                    el_title = c.xpath('.//a[@class="bookTitle"]//text()')
                    title = "".join(el_title).strip() if el_title else None
                    el_url = c.xpath('.//a[@class="bookTitle"]/@href')
                    url = "https://www.goodreads.com" + el_url[0] if el_url else None
                    el_authors = c.xpath('.//a[@class="authorName"]//text()')
                    subtitle = ", ".join(el_authors) if el_authors else None
                    results.append(
                        SearchResultItem(
                            ItemCategory.Book,
                            SiteName.Goodreads,
                            url,
                            title,
                            subtitle,
                            "",
                            cover,
                        )
                    )
        except Exception as e:
            logger.error(f"Goodreads search '{q}' error: {e}")
        return results


class GoogleBooks:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            api_url = f"https://www.googleapis.com/books/v1/volumes?country=us&q={quote_plus(q)}&startIndex={SEARCH_PAGE_SIZE*(page-1)}&maxResults={SEARCH_PAGE_SIZE}&maxAllowedMaturityRating=MATURE"
            j = requests.get(api_url).json()
            if "items" in j:
                for b in j["items"]:
                    if "title" not in b["volumeInfo"]:
                        continue
                    title = b["volumeInfo"]["title"]
                    subtitle = ""
                    if "publishedDate" in b["volumeInfo"]:
                        subtitle += b["volumeInfo"]["publishedDate"] + " "
                    if "authors" in b["volumeInfo"]:
                        subtitle += ", ".join(b["volumeInfo"]["authors"])
                    if "description" in b["volumeInfo"]:
                        brief = b["volumeInfo"]["description"]
                    elif "textSnippet" in b["volumeInfo"]:
                        brief = b["volumeInfo"]["textSnippet"]["searchInfo"]
                    else:
                        brief = ""
                    category = ItemCategory.Book
                    # b['volumeInfo']['infoLink'].replace('http:', 'https:')
                    url = "https://books.google.com/books?id=" + b["id"]
                    cover = (
                        b["volumeInfo"]["imageLinks"]["thumbnail"]
                        if "imageLinks" in b["volumeInfo"]
                        else None
                    )
                    results.append(
                        SearchResultItem(
                            category,
                            SiteName.GoogleBooks,
                            url,
                            title,
                            subtitle,
                            brief,
                            cover,
                        )
                    )
        except Exception as e:
            logger.error(f"GoogleBooks search '{q}' error: {e}")
        return results


class TheMovieDatabase:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            api_url = f"https://api.themoviedb.org/3/search/multi?query={quote_plus(q)}&page={page}&api_key={settings.TMDB_API3_KEY}&language=zh-CN&include_adult=true"
            j = requests.get(api_url).json()
            for m in j["results"]:
                if m["media_type"] in ["tv", "movie"]:
                    url = f"https://www.themoviedb.org/{m['media_type']}/{m['id']}"
                    if m["media_type"] == "tv":
                        cat = ItemCategory.TV
                        title = m["name"]
                        subtitle = f"{m.get('first_air_date', '')} {m.get('original_name', '')}"
                    else:
                        cat = ItemCategory.Movie
                        title = m["title"]
                        subtitle = (
                            f"{m.get('release_date', '')} {m.get('original_name', '')}"
                        )
                    cover = f"https://image.tmdb.org/t/p/w500/{m.get('poster_path')}"
                    results.append(
                        SearchResultItem(
                            cat,
                            SiteName.TMDB,
                            url,
                            title,
                            subtitle,
                            m.get("overview"),
                            cover,
                        )
                    )
        except Exception as e:
            logger.error(f"TMDb search '{q}' error: {e}")
        return results


class Spotify:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            api_url = f"https://api.spotify.com/v1/search?q={q}&type=album&limit={SEARCH_PAGE_SIZE}&offset={page*SEARCH_PAGE_SIZE}"
            headers = {"Authorization": f"Bearer {get_spotify_token()}"}
            j = requests.get(api_url, headers=headers).json()
            for a in j["albums"]["items"]:
                title = a["name"]
                subtitle = a["release_date"]
                for artist in a["artists"]:
                    subtitle += " " + artist["name"]
                url = a["external_urls"]["spotify"]
                cover = a["images"][0]["url"]
                results.append(
                    SearchResultItem(
                        ItemCategory.Music,
                        SiteName.Spotify,
                        url,
                        title,
                        subtitle,
                        "",
                        cover,
                    )
                )
        except Exception as e:
            logger.error(f"Spotify search '{q}' error: {e}")
        return results


class Bandcamp:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            search_url = f"https://bandcamp.com/search?from=results&item_type=a&page={page}&q={quote_plus(q)}"
            r = requests.get(search_url)
            h = html.fromstring(r.content.decode("utf-8"))
            for c in h.xpath('//li[@class="searchresult data-search"]'):
                el_cover = c.xpath('.//div[@class="art"]/img/@src')
                cover = el_cover[0] if el_cover else None
                el_title = c.xpath('.//div[@class="heading"]//text()')
                title = "".join(el_title).strip() if el_title else None
                el_url = c.xpath('..//div[@class="itemurl"]/a/@href')
                url = el_url[0] if el_url else None
                el_authors = c.xpath('.//div[@class="subhead"]//text()')
                subtitle = ", ".join(el_authors) if el_authors else None
                results.append(
                    SearchResultItem(
                        ItemCategory.Music,
                        SiteName.Bandcamp,
                        url,
                        title,
                        subtitle,
                        "",
                        cover,
                    )
                )
        except Exception as e:
            logger.error(f"Goodreads search '{q}' error: {e}")
        return results


class ApplePodcast:
    @classmethod
    def search(cls, q, page=1):
        results = []
        try:
            search_url = f"https://itunes.apple.com/search?entity=podcast&limit={page*SEARCH_PAGE_SIZE}&term={quote_plus(q)}"
            r = requests.get(search_url).json()
            for p in r["results"][(page - 1) * SEARCH_PAGE_SIZE :]:
                results.append(
                    SearchResultItem(
                        ItemCategory.Podcast,
                        SiteName.RSS,
                        p["feedUrl"],
                        p["trackName"],
                        p["artistName"],
                        "",
                        p["artworkUrl600"],
                    )
                )
        except Exception as e:
            logger.error(f"ApplePodcast search '{q}' error: {e}")
        return results


class ExternalSources:
    @classmethod
    def search(cls, c, q, page=1):
        if not q:
            return []
        results = []
        if c == "" or c is None:
            c = "all"
        if c == "all" or c == "movietv":
            results.extend(TheMovieDatabase.search(q, page))
        if c == "all" or c == "book":
            results.extend(GoogleBooks.search(q, page))
            results.extend(Goodreads.search(q, page))
        if c == "all" or c == "music":
            results.extend(Spotify.search(q, page))
            results.extend(Bandcamp.search(q, page))
        if c == "podcast":
            results.extend(ApplePodcast.search(q, page))
        return results
