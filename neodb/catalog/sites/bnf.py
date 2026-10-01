"""
BnF catalogue général, the French national library catalogue.

Records come from the SRU API as UNIMARC, never from the HTML page. No key is
required and the data is published under the Licence Ouverte (Etalab), which
asks reusers to credit the BnF; the about page carries that credit.

UNIMARC as the BnF writes it has changed over the years, and the catalogue
holds every generation at once:
- retro-converted records leave the leader's record type blank and may lack
  the 100 date
- records from about 2025 on drop 200$f and the 70x relator code, putting the
  role in words into 70x$c ("Auteur du texte") instead
- 215$a reads "1 vol. (185 p.)" or "1 volume 519 p", and 010$b abbreviates
  the binding in several spellings ("br.", "br", "Br.", "rel.", "rl")

Search runs only for ISBN queries: every book search fans out to every
searchable site, and a free-text BnF search would add one request to each of
them on every instance for results that are mostly French.
"""

import logging
import re
from urllib.parse import urlencode

import httpx
import pycountry
from lxml import etree

from catalog.common import *
from catalog.common.downloaders import DownloadError
from catalog.models import *
from catalog.models.utils import detect_isbn_asin, isbn_13_to_10
from catalog.search import ExternalSearchResultItem, record_search_failure
from common.models import normalize_price
from common.models.lang import normalize_language

_logger = logging.getLogger(__name__)

_SRU_URL = "https://catalogue.bnf.fr/api/SRU"
_COVER_URL = "https://catalogue.bnf.fr/couverture?appName=NE&idImage={}&couverture=1"

# UNIMARC relator codes
_RELATOR_AUTHOR = "070"
_RELATOR_TRANSLATOR = "730"

# 200$e of a French record is often only the form of the work, which the
# cover prints but which is no subtitle
_GENRE_WORDS = {
    "autobiographie",
    "bande dessinée",
    "biographie",
    "chronique",
    "chroniques",
    "conte",
    "contes",
    "entretien",
    "entretiens",
    "essai",
    "essais",
    "fable",
    "fables",
    "mémoires",
    "nouvelle",
    "nouvelles",
    "pièce",
    "poème",
    "poèmes",
    "poésie",
    "poésies",
    "polar",
    "récit",
    "récits",
    "roman",
    "roman graphique",
    "roman jeunesse",
    "roman noir",
    "roman policier",
    "romans",
    "témoignage",
    "théâtre",
    "thriller",
}

_BINDINGS = {
    "br": ("broché", Edition.BookFormat.PAPERBACK),
    "broché": ("broché", Edition.BookFormat.PAPERBACK),
    "rel": ("relié", Edition.BookFormat.HARDCOVER),
    "rl": ("relié", Edition.BookFormat.HARDCOVER),
    "relié": ("relié", Edition.BookFormat.HARDCOVER),
    "cart": ("cartonné", Edition.BookFormat.HARDCOVER),
    "cartonné": ("cartonné", Edition.BookFormat.HARDCOVER),
}


def _sru_url(query: str, maximum_records: int = 1) -> str:
    # a fixed parameter order keeps the URL, and so the test fixture name, stable
    params = {
        "version": "1.2",
        "operation": "searchRetrieve",
        "query": query,
        "recordSchema": "unimarcxchange",
        "maximumRecords": maximum_records,
    }
    return f"{_SRU_URL}?{urlencode(params)}"


def _record_count(root) -> int:
    n = str(root.xpath("string(//*[local-name()='numberOfRecords'])")).strip()
    return int(n) if n.isdigit() else 0


def _records(root) -> list["UnimarcRecord"]:
    return [
        UnimarcRecord(e)
        for e in root.xpath("//*[local-name()='record'][@type='Bibliographic']")
    ]


class BnFDownloader(BasicDownloader):
    def validate_response(self, response) -> int:
        r = super().validate_response(response)
        if r != RESPONSE_OK or response is None:
            return r
        try:
            root = etree.fromstring(response.content)
        except etree.XMLSyntaxError:
            return RESPONSE_INVALID_CONTENT
        # an unknown ark is answered with an empty result set, not a 404
        return RESPONSE_OK if _record_count(root) > 0 else RESPONSE_INVALID_CONTENT


def _strip_isbd(s: str) -> str:
    """Drop the ISBD punctuation that ends a subfield.

    A full stop counts only when spaced off ("perdu ."), so that an
    ellipsis or an abbreviation at the end survives.
    """
    return re.sub(r"(\s+\.)+$", "", s.strip().rstrip(" /:;,=")).strip()


class UnimarcRecord:
    def __init__(self, element):
        self.element = element

    @property
    def leader(self) -> str:
        return str(self.element.xpath("string(*[local-name()='leader'])")).ljust(8)

    @property
    def ark(self) -> str | None:
        for v in [self.element.get("id", "")] + self.control("003"):
            m = re.search(r"ark:/12148/(cb\w+)", v)
            if m:
                return m[1]
        return None

    def control(self, tag: str) -> list[str]:
        return [
            (cf.text or "").strip()
            for cf in self.element.xpath(
                f"*[local-name()='controlfield'][@tag='{tag}']"
            )
        ]

    def fields(self, *tags: str) -> list:
        return [
            df
            for df in self.element.xpath("*[local-name()='datafield']")
            if df.get("tag") in tags
        ]

    @staticmethod
    def subfields(field, code: str) -> list[str]:
        values = []
        for s in field.xpath(f"*[local-name()='subfield'][@code='{code}']"):
            text = " ".join((s.text or "").split())
            if text:
                values.append(text)
        return values

    @staticmethod
    def subfield(field, code: str) -> str:
        values = UnimarcRecord.subfields(field, code)
        return values[0] if values else ""

    def all(self, tag: str, code: str) -> list[str]:
        return [v for f in self.fields(tag) for v in self.subfields(f, code)]

    def first(self, tag: str, code: str) -> str:
        values = self.all(tag, code)
        return values[0] if values else ""

    @property
    def is_book(self) -> bool:
        # i is a non-musical sound recording; l is software, not ebooks
        return self.leader[6] in ("a", " ", "i")

    @property
    def format(self) -> Edition.BookFormat | None:
        if self.leader[6] == "i":
            return Edition.BookFormat.AUDIOBOOK
        # RDA media type "c" is computer, the BnF's mark for an ebook
        media = [
            v
            for f in self.fields("182")
            if self.subfield(f, "2") == "rdamedia"
            for v in self.subfields(f, "c")
        ]
        designation = " ".join(self.all("200", "b")).lower()
        if "c" in media or "électronique" in designation:
            return Edition.BookFormat.EBOOK
        return self.binding[1]

    @property
    def binding(self) -> tuple[str | None, Edition.BookFormat | None]:
        b = self.first("010", "b")
        return _BINDINGS.get(b.lower().rstrip(" ."), (b or None, None))

    @property
    def title(self) -> str:
        """200 title proper with its part number and part name, ISBD style."""
        fields = self.fields("200")
        if not fields:
            return ""
        title = ""
        has_number = False
        for s in fields[0].xpath("*[local-name()='subfield']"):
            code, text = s.get("code"), _strip_isbd(" ".join((s.text or "").split()))
            if not text:
                continue
            if code == "a":
                title = f"{title} ; {text}" if title else text
            elif code == "h":
                title = f"{title}. {text}"
                has_number = True
            elif code == "i":
                title = f"{title}, {text}" if has_number else f"{title}. {text}"
        return title

    @property
    def subtitle(self) -> str:
        parts = [_strip_isbd(e) for e in self.all("200", "e")]
        return " : ".join(p for p in parts if p and p.lower() not in _GENRE_WORDS)

    @property
    def contributors(self) -> tuple[list[str], list[str]]:
        statement = " ".join(self.all("200", "f") + self.all("200", "g"))
        authors = []
        translators = []
        for f in self.fields("700", "701", "702", "710", "711", "712"):
            surname = self.subfield(f, "a")
            if not surname:
                continue
            if f.get("tag").startswith("71"):
                name = " ".join([surname] + self.subfields(f, "b"))
            else:
                name = _display_name(surname, self.subfield(f, "b"), statement)
            relators = self.subfields(f, "4")
            role = " ".join(self.subfields(f, "c")).lower()
            if _RELATOR_TRANSLATOR in relators or role.startswith("trad"):
                translators.append(name)
            elif (
                _RELATOR_AUTHOR in relators
                or role.startswith("auteur")
                # older records give the main entry no role at all
                or (not relators and not role and f.get("tag") in ("700", "710"))
            ):
                authors.append(name)
        return list(dict.fromkeys(authors)), list(dict.fromkeys(translators))

    def publication_fields(self, *roles: str) -> list:
        """210, then 214 by role (second indicator): 0 publication,
        1 production, 2 distribution, 3 manufacture, 4 copyright date."""
        return self.fields("210") + [
            f for f in self.fields("214") if f.get("ind2") in roles
        ]

    @property
    def publisher(self) -> str:
        # 210 carries the printer in $g, not $c
        for f in self.publication_fields("0"):
            p = self.subfield(f, "c")
            if p:
                return _strip_isbd(p)
        return ""

    @property
    def pub_year(self) -> int | None:
        coded = self.first("100", "a")
        if len(coded) >= 13 and coded[9:13].isdigit():
            return int(coded[9:13])
        for f in self.publication_fields("0") + self.publication_fields("4"):
            for d in self.subfields(f, "d"):
                m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", d)
                if m:
                    return int(m[1])
        return None

    @property
    def pages(self) -> int | None:
        m = re.search(r"(\d+)\s*p\b", self.first("215", "a"))
        return int(m[1]) if m else None

    @property
    def isbn(self) -> str | None:
        # 073 is an EAN, which is an ISBN only under the Bookland prefixes
        for v in self.all("010", "a") + self.all("073", "a"):
            t, n = detect_isbn_asin(v)
            if t == IdType.ISBN and n and n[:3] in ("978", "979"):
                return n
        return None

    @property
    def price(self) -> str | None:
        p = self.first("010", "d")
        if not p:
            return None
        # French decimal comma, which normalize_price reads as a thousands mark
        return normalize_price(re.sub(r"(\d),(\d{1,2})\b", r"\1.\2", p), "EUR")

    @property
    def languages(self) -> list[str]:
        return list(
            dict.fromkeys(
                lang for v in self.all("101", "a") if (lang := _marc_language(v))
            )
        )

    @property
    def original_titles(self) -> list[str]:
        titles = list(dict.fromkeys(_strip_isbd(t) for t in self.all("454", "t")))
        # a transliteration often precedes the title in its own script
        titles.sort(key=lambda t: not re.search(r"[^\u0000-ɏ]", t))
        return titles

    @property
    def series(self) -> str | None:
        for f in self.fields("225"):
            name = _strip_isbd(self.subfield(f, "a"))
            if name:
                number = self.subfield(f, "v")
                return f"{name} ; {number}" if number else name
        return None

    @property
    def cover_url(self) -> str | None:
        for f in self.fields("856"):
            # 856 also links digitised copies on Gallica
            image_id = self.subfield(f, "u")
            label = self.subfield(f, "b").lower()
            if image_id.isdigit() and label.startswith("première de couverture"):
                return _COVER_URL.format(image_id)
        return None


def _marc_language(code: str) -> str | None:
    """ISO 639-2 code, bibliographic or terminology form, as NeoDB stores it."""
    code = code.strip().lower()
    # multiple, undetermined, no linguistic content
    if code in ("mul", "und", "zxx"):
        return None
    lang = pycountry.languages.get(alpha_3=code) or pycountry.languages.get(
        bibliographic=code
    )
    return normalize_language(getattr(lang, "alpha_2", None) or code)


def _display_name(surname: str, forename: str, statement: str) -> str:
    """Name as the title page prints it, else "Forename Surname".

    Authority headings invert every name, which reads wrong for names written
    surname first: "Liu, Ci xin" is printed "Liu Cixin". The statement of
    responsibility keeps the printed form, so look for the name there.
    """
    if not forename:
        return surname
    for first, second in ((forename, surname), (surname, forename)):
        letters = (first + second).replace(" ", "")
        pattern = r"\s*".join(re.escape(c) for c in letters)
        m = re.search(rf"(?<!\w){pattern}(?!\w)", statement, re.IGNORECASE)
        if m:
            return m[0]
    return f"{forename} {surname}"


@SiteManager.register
class BnF(AbstractSite):
    SITE_NAME = SiteName.BnF
    ID_TYPE = IdType.BnF
    URL_PATTERNS = [
        r"\w+://catalogue\.bnf\.fr/ark:/12148/(cb\d{8}[0-9a-z])",
    ]
    # P268 holds BnF authority ids (people and works), not bibliographic records
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://catalogue.bnf.fr/ark:/12148/" + id_value

    @classmethod
    def record_to_metadata(cls, record: UnimarcRecord) -> dict:
        authors, translators = record.contributors
        lang = (record.languages or ["fr"])[0]
        title = record.title
        subtitle = record.subtitle
        brief = "\n".join(record.all("330", "a"))
        original_titles = record.original_titles
        variant_titles = [_strip_isbd(t) for t in record.all("517", "a")]
        other_titles = [
            t
            for t in dict.fromkeys(original_titles[1:] + variant_titles)
            if t and t != title
        ]
        binding, _ = record.binding
        return {
            "title": title,
            "localized_title": [{"lang": lang, "text": title}],
            "subtitle": subtitle or None,
            "localized_subtitle": (
                [{"lang": lang, "text": subtitle}] if subtitle else []
            ),
            "orig_title": original_titles[0] if original_titles else None,
            "other_title": other_titles,
            "author": authors,
            "translator": translators,
            "language": record.languages,
            "publisher": [record.publisher] if record.publisher else [],
            "pub_year": record.pub_year,
            "binding": binding,
            "format": record.format,
            "pages": record.pages,
            "price": record.price,
            "series": record.series,
            "isbn": record.isbn,
            "brief": brief,
            "localized_description": [{"lang": lang, "text": brief}] if brief else [],
            "contents": "\n".join(record.all("327", "a")) or None,
            "cover_image_url": record.cover_url,
        }

    def scrape(self):
        downloader = BnFDownloader(
            _sru_url(f'bib.persistentid all "ark:/12148/{self.id_value}"')
        )
        records = _records(downloader.download().xml())
        if not records:
            raise ParseError(self, "record")
        record = records[0]
        if not record.is_book:
            downloader.response_type = RESPONSE_INVALID_CONTENT
            raise DownloadError(downloader, "not a book record")
        data = self.record_to_metadata(record)
        if not data["title"]:
            raise ParseError(self, "title")
        pd = ResourceContent(metadata=data)
        if data["isbn"]:
            pd.lookup_ids[IdType.ISBN] = data["isbn"]
        if data["cover_image_url"]:
            pd.cover_image, pd.cover_image_extention = (
                BasicImageDownloader.download_image(data["cover_image_url"], self.url)
            )
        return pd

    @classmethod
    async def search_task(
        cls, q: str, page: int, category: str, page_size: int
    ) -> list[ExternalSearchResultItem]:
        if category not in ["all", "book"] or page > 1:
            return []
        t, isbn = detect_isbn_asin(q)
        if t != IdType.ISBN:
            return []
        results = []
        # records older than ISBN-13 match only their ISBN-10
        query = " or ".join(
            f'bib.isbn all "{i}"'
            for i in dict.fromkeys([isbn, isbn_13_to_10(isbn)])
            if i
        )
        url = _sru_url(query, page_size)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=3)
                response.raise_for_status()
                for record in _records(etree.fromstring(response.content)):
                    ark = record.ark
                    if not ark or not record.is_book:
                        continue
                    authors, _ = record.contributors
                    subtitle = " • ".join(
                        p
                        for p in [
                            ", ".join(authors[:2]),
                            record.publisher,
                            str(record.pub_year or ""),
                        ]
                        if p
                    )
                    results.append(
                        ExternalSearchResultItem(
                            ItemCategory.Book,
                            SiteName.BnF,
                            cls.id_to_url(ark),
                            record.title,
                            subtitle,
                            record.subtitle,
                            record.cover_url or "",
                        )
                    )
            except httpx.TimeoutException:
                _logger.warning("BnF search timeout", extra={"query": q})
                record_search_failure(cls.SITE_NAME.value, "timeout")
            except httpx.HTTPError as e:
                _logger.warning("BnF search error", extra={"query": q, "exception": e})
                record_search_failure(cls.SITE_NAME.value, "error")
            except Exception as e:
                _logger.error("BnF search error", extra={"query": q, "exception": e})
                record_search_failure(cls.SITE_NAME.value, "error")
        return results
