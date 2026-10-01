import asyncio
from pathlib import Path

import httpx
import pytest

from catalog.common import SiteManager, use_local_response
from catalog.common.downloaders import DownloadError, get_mock_file
from catalog.models import Edition, IdType, ItemCategory, SiteName
from lxml import etree

from catalog.sites.bnf import BnF, UnimarcRecord, _records, _sru_url

_TEST_DATA = Path(__file__).parent.parent.parent / "test_data"


def _record(*fields: tuple[str, list[tuple[str, str]]]) -> UnimarcRecord:
    """Fields as ("tag", subfields), or ("tag#ind2", subfields)."""
    xml = "".join(
        f'<datafield tag="{tag.partition("#")[0]}" ind2="{tag.partition("#")[2]}">'
        + "".join(f'<subfield code="{c}">{v}</subfield>' for c, v in subs)
        + "</datafield>"
        for tag, subs in fields
    )
    return UnimarcRecord(etree.fromstring(f"<record>{xml}</record>"))


def _metadata(ark: str) -> dict:
    site = SiteManager.get_site_by_url(BnF.id_to_url(ark))
    assert site is not None
    site.get_resource_ready()
    assert site.resource is not None
    return site.resource.metadata


@pytest.mark.django_db(databases="__all__")
class TestBnFParse:
    def test_parse(self):
        t_url = "https://catalogue.bnf.fr/ark:/12148/cb41049264d"
        for u in [
            t_url,
            "http://catalogue.bnf.fr/ark:/12148/cb41049264d",
            "https://catalogue.bnf.fr/ark:/12148/cb41049264d.public",
            "https://catalogue.bnf.fr/ark:/12148/cb41049264d?fq=dewey",
        ]:
            site = SiteManager.get_site_by_url(u)
            assert site is not None
            assert site.ID_TYPE == IdType.BnF
            assert site.id_value == "cb41049264d"
            assert site.url == t_url
        assert SiteManager.get_site_by_url("https://catalogue.bnf.fr/") is None

    def test_edition_lookup_type(self):
        # pre-ISBN records are identified by the ark alone, so the edit form
        # must offer it or it silently rewrites the primary lookup id
        assert IdType.BnF.value in dict(Edition.lookup_id_type_choices())


class TestUnimarcRecord:
    def test_ean_that_is_not_an_isbn(self):
        # a checksum-valid EAN outside 978/979 is no ISBN
        assert _record(("073", [("a", "0123456789012")])).isbn is None
        assert (
            _record(
                ("073", [("a", "0123456789012")]),
                ("010", [("a", "978-2-02-090219-9")]),
            ).isbn
            == "9782020902199"
        )
        assert _record(("010", [("a", "2-07-036002-4")])).isbn == "9782070360024"
        film_url = _sru_url('bib.persistentid all "ark:/12148/cb42284376s"')
        film = (_TEST_DATA / get_mock_file(film_url)).read_bytes()
        assert _records(etree.fromstring(film))[0].isbn is None

    def test_publisher_is_the_publication_statement(self):
        record = _record(
            ("214#1", [("c", "Studio de production"), ("d", "2010")]),
            ("214#0", [("a", "Paris"), ("c", "Gallimard")]),
            ("214#3", [("c", "Impr. CPI")]),
            ("214#4", [("d", "C 2012")]),
        )
        assert record.publisher == "Gallimard"
        # the copyright date, not the production date
        assert record.pub_year == 2012

    def test_trailing_isbd_full_stop(self):
        assert _record(("225", [("a", "À la recherche du temps perdu .")])).series == (
            "À la recherche du temps perdu"
        )
        assert _record(("200", [("a", "Et après...")])).title == "Et après..."

    def test_languages(self):
        codes = ["fre", "lat", "heb", "dan", "fin", "mul"]
        record = _record(("101", [("a", c) for c in codes]))
        assert record.languages == ["fr", "la", "he", "da", "fi"]
        assert _record(("101", [("a", "ger"), ("a", "deu")])).languages == ["de"]


@pytest.mark.django_db(databases="__all__")
class TestBnFScrape:
    @use_local_response
    def test_scrape(self):
        site = SiteManager.get_site_by_url(
            "https://catalogue.bnf.fr/ark:/12148/cb41049264d"
        )
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        md = site.resource.metadata
        assert (
            md["title"]
            == "Le séminaire. Livre XVIII, D'un discours qui ne serait pas du semblant"
        )
        assert md["author"] == ["Jacques Lacan"]
        # Jacques-Alain Miller established the text (relator 340), no author
        assert md["translator"] == []
        assert md["publisher"] == ["Éd. du Seuil"]
        assert md["pub_year"] == 2007
        assert md["pages"] == 185
        assert md["language"] == ["fr"]
        assert md["format"] == Edition.BookFormat.HARDCOVER
        assert md["binding"] == "relié"
        assert md["price"] == "EUR 21"
        assert md["series"] == "Champ freudien"
        assert md["isbn"] == "9782020902199"
        assert site.resource.other_lookup_ids == {IdType.ISBN: "9782020902199"}
        assert site.resource.item is not None
        assert site.resource.item.isbn == "9782020902199"

    @use_local_response
    def test_summary_and_cover(self):
        md = _metadata("cb475392715")
        assert md["title"] == "Houris"
        # 200$e is only "roman"
        assert md["subtitle"] is None
        assert md["author"] == ["Kamel Daoud"]
        # 214 with second indicator 3 names the printer
        assert md["publisher"] == ["Gallimard"]
        assert md["format"] == Edition.BookFormat.PAPERBACK
        assert md["localized_description"][0]["lang"] == "fr"
        assert md["brief"].startswith("« Je suis la véritable trace")
        assert md["cover_image_url"] == (
            "https://catalogue.bnf.fr/couverture?appName=NE&idImage=922062&couverture=1"
        )

    @use_local_response
    def test_role_in_words(self):
        # a 2026 record: no 200$f, no relator code, no date in field 100
        md = _metadata("cb48831849c")
        assert md["author"] == ["Kamel Daoud"]
        assert md["pub_year"] == 2026
        assert md["pages"] == 519
        assert md["binding"] == "broché"
        assert md["price"] == "EUR 10"
        assert md["series"] == "Folio ; 7704"
        # the back cover is listed after the front one
        assert md["cover_image_url"].endswith("idImage=1305557&couverture=1")

    @use_local_response
    def test_translation(self):
        md = _metadata("cb45379797f")
        # the heading is "Liu, Ci xin"; the book prints the name surname first
        assert md["author"] == ["Liu Cixin"]
        assert md["translator"] == ["Gwennaël Gaffric"]
        # the original script is preferred over the transliteration before it
        assert md["orig_title"] == "黑暗森林"
        assert md["other_title"] == ["Hei'an senlin"]
        assert md["price"] == "EUR 23.80"
        assert md["series"] == "Le problème à trois corps"

    @use_local_response
    def test_retro_converted_record(self):
        site = SiteManager.get_site_by_url(
            "https://catalogue.bnf.fr/ark:/12148/cb31902899n"
        )
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        md = site.resource.metadata
        assert md["title"] == "L'étranger"
        assert md["author"] == ["Albert Camus"]
        assert md["pub_year"] == 1942
        assert md["pages"] == 159
        assert md["isbn"] is None
        # 856 links the Gallica scan, not a cover
        assert md["cover_image_url"] is None
        assert site.resource.item is not None
        assert site.resource.item.primary_lookup_id_type == IdType.BnF
        assert site.resource.item.primary_lookup_id_value == "cb31902899n"

    @use_local_response
    def test_audiobook_and_ebook(self):
        md = _metadata("cb45755542x")
        assert md["format"] == Edition.BookFormat.AUDIOBOOK
        assert md["author"] == ["Liu Cixin"]
        assert md["translator"] == ["Gwennaël Gaffric"]
        assert md["pages"] is None
        assert _metadata("cb44668809j")["format"] == Edition.BookFormat.EBOOK

    @use_local_response
    def test_not_a_book(self):
        site = SiteManager.get_site_by_url(
            "https://catalogue.bnf.fr/ark:/12148/cb42284376s"
        )
        assert site is not None
        with pytest.raises(DownloadError, match="not a book record"):
            site.scrape()

    @use_local_response
    def test_unknown_record(self):
        site = SiteManager.get_site_by_url(
            "https://catalogue.bnf.fr/ark:/12148/cb00000000x"
        )
        assert site is not None
        with pytest.raises(DownloadError):
            site.scrape()


class TestBnFSearch:
    def _patch(self, monkeypatch) -> list[str]:
        requests: list[str] = []

        async def get(self, url, **kwargs):
            requests.append(url)
            content = (_TEST_DATA / get_mock_file(url)).read_bytes()
            return httpx.Response(
                200, request=httpx.Request("GET", url), content=content
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", get)
        return requests

    def test_search_by_isbn(self, monkeypatch):
        requests = self._patch(monkeypatch)
        results = asyncio.run(BnF.search_task("978-2-07-299999-4", 1, "all", 10))
        assert requests == [
            _sru_url('bib.isbn all "9782072999994" or bib.isbn all "2072999995"', 10)
        ]
        assert len(results) == 1
        r = results[0]
        assert r.category == ItemCategory.Book
        assert r.source_site == SiteName.BnF
        assert r.source_url == "https://catalogue.bnf.fr/ark:/12148/cb475392715"
        assert r.display_title == "Houris"
        assert r.subtitle == "Kamel Daoud • Gallimard • 2024"
        assert r.cover_image_url.endswith("idImage=922062&couverture=1")

    def test_search_skips_other_queries(self, monkeypatch):
        requests = self._patch(monkeypatch)
        assert asyncio.run(BnF.search_task("houris", 1, "all", 10)) == []
        assert asyncio.run(BnF.search_task("9782072999994", 1, "movie", 10)) == []
        assert asyncio.run(BnF.search_task("9782072999994", 2, "book", 10)) == []
        assert requests == []
