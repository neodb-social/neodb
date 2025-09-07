import json
import logging
import re
import time

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from catalog.common import *
from catalog.models import *
from catalog.models.utils import resource_cover_path

_logger = logging.getLogger(__name__)


@staticmethod
def query_str(content, query: str) -> str:
    return content.xpath(query)[0].strip()


def get_bibliotekdk_token():
    cache_key = "bibliotekdk:accessToken"
    token = cache.get(cache_key)
    if token:
        return token

    h = (
        BasicDownloader("https://bibliotek.dk/", {"User-Agent": "curl/8.7.1"})
        .download()
        .html()
    )
    src = query_str(h, '//script[@id="__NEXT_DATA__"]/text()')
    if not src:
        raise ParseError(
            type("BibliotekDKToken", (), {"url": "https://bibliotek.dk"}),
            "__NEXT_DATA__ element",
        )
    session = json.loads(src)["props"]["pageProps"]["session"]

    token = session["accessToken"]
    timeout = session["exp"] - int(time.time())

    if timeout > 60 * 60 * 24:
        timeout = 60 * 60 * 24

    if timeout > 60:
        cache.set(cache_key, token, timeout)

    return token


class BibliotekDKImageDownloader(BasicImageDownloader):
    def validate_response(self, response):
        # Fix broken content type on bibliotek.dk
        if response.headers.get("Content-Type") == "image/jpg":
            response.headers["Content-Type"] = "image/jpeg"

        # Fix broken content type on moreinfo.addi.dk
        if response.headers.get("Content-Type") == "image/JPEG":
            response.headers["Content-Type"] = "image/jpeg"

        return super().validate_response(response)


class BibliotekDKImageStore:
    @classmethod
    def save(cls, id_type, filename, content):
        pseudo_res = type("pseudo_res", (), {"id_type": id_type})
        path = resource_cover_path(pseudo_res, filename)

        default_storage.save(path, content)

        return path


class BibliotekDKSite(AbstractSite):
    @classmethod
    def url_to_id(cls, url: str):
        id_value = super().url_to_id(url)

        if id_value is None:
            return None

        # Normalize the id_value
        return id_value.replace("%3A", ":")


@SiteManager.register
class BibliotekDK_Edition(BibliotekDKSite):
    SITE_NAME = SiteName.BibliotekDK
    ID_TYPE = IdType.BibliotekDK_Edition
    URL_PATTERNS = [
        r"https://bibliotek.dk/work/pid/([^?]+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://bibliotek.dk/work/pid/" + id_value + "?scrollToEdition=True"

    @classmethod
    def get_edition(cls, id_value, data):
        # These are copied from the work object
        jsonLd = data["workJsonLd"]["data"]["work"]

        workId = jsonLd["workId"]
        title = jsonLd["titles"]["full"][0]
        description = jsonLd["abstract"][0] if jsonLd["abstract"] else None

        pub_year = None
        pub_house = None
        authors = []
        for edition in data["listOfAllManifestations"]["data"]["work"][
            "manifestations"
        ]["mostRelevant"]:
            if edition["pid"] != id_value:
                continue

            pub_year = edition["edition"]["publicationYear"]["year"]
            pub_house = edition["publisher"][0] if edition["publisher"] else None

            for creator in edition["creators"]:
                authors.append(creator["display"])

        isbn = None
        img_url = None
        for edition in data["workJsonLd"]["data"]["work"]["manifestations"]["all"]:
            if edition["pid"] != id_value:
                continue

            for id in edition["identifiers"]:
                if id["type"] == "ISBN":
                    isbn = id["value"]

            img_url = edition["cover"]["detail"]

        img_path = None
        if img_url is not None:
            raw_img, ext = BibliotekDKImageDownloader.download_image(
                img_url, cls.id_to_url(id_value)
            )
            if raw_img and ext:
                file = SimpleUploadedFile("temp." + ext, raw_img)
                img_path = BibliotekDKImageStore.save(cls.ID_TYPE, "temp." + ext, file)

        language = "da"
        data = {
            "title": title,
            "localized_title": [{"lang": language, "text": title}],
            "author": authors,
            "language": language,
            "pub_year": pub_year,
            "pub_house": pub_house,
            "isbn": isbn,
            "localized_description": (
                [{"lang": language, "text": description}] if description else []
            ),
            "cover_image_url": img_url,
            "cover_image_path": img_path,
            "required_resources": {
                "model": "Work",
                "id_type": IdType.BibliotekDK_Work,
                "id_value": workId,
            },
        }

        return ResourceContent(
            metadata=data,
            lookup_ids={
                IdType.ISBN: isbn,
                IdType.BibliotekDK_Edition: id_value,
            },
        )

    def scrape(self):
        h = BasicDownloader(self.url, {"User-Agent": "curl/8.7.1"}).download().html()
        src = self.query_str(h, '//script[@id="__NEXT_DATA__"]/text()')
        if not src:
            raise ParseError(self, "__NEXT_DATA__ element")
        d = json.loads(src)["props"]["pageProps"]["initialData"]

        for key in list(d):
            m = re.search(r"query (\w+)", key)
            if m:
                d[m.group(1)] = d.pop(key)

        return self.get_edition(self.id_value, d)


@SiteManager.register
class BibliotekDK_eReolen(BibliotekDKSite):
    SITE_NAME = SiteName.BibliotekDK_eReolen
    ID_TYPE = IdType.BibliotekDK_eReolen
    URL_PATTERNS = [
        r"https://ereolen.dk/ting/object/([^?]+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://ereolen.dk/ting/object/" + id_value

    def scrape(self):
        return BibliotekDK_Edition(id_value=self.id_value).scrape()


@SiteManager.register
class BibliotekDK_Work(BibliotekDKSite):
    SITE_NAME = SiteName.BibliotekDK
    ID_TYPE = IdType.BibliotekDK_Work
    URL_PATTERNS = [
        r"https://bibliotek.dk/materiale/[^/]+/(work-of[^?/]+)",
    ]
    WIKI_PROPERTY_ID = ""
    DEFAULT_MODEL = Work

    @classmethod
    def id_to_url(cls, id_value):
        return "https://bibliotek.dk/materiale/title/" + id_value

    @classmethod
    def get_work(cls, data):
        title = data["titles"]["full"][0]

        authors = []
        for creator in data["creators"]:
            authors.append(creator["display"])

        description = data["abstract"][0]
        language = "da"

        data = {
            "title": title,
            "localized_title": [{"lang": language, "text": title}],
            "author": authors,
            "localized_description": (
                [{"lang": language, "text": description}] if description else []
            ),
        }

        return ResourceContent(
            metadata=data,
        )

    def scrape(self):
        h = BasicDownloader(self.url, {"User-Agent": "curl/8.7.1"}).download().html()
        src = self.query_str(h, '//script[@id="__NEXT_DATA__"]/text()')
        if not src:
            raise ParseError(self, "__NEXT_DATA__ element")
        d = json.loads(src)["props"]["pageProps"]["initialData"]

        for key in list(d):
            m = re.search(r"query (\w+)", key)
            if m:
                d[m.group(1)] = d.pop(key)

        work = d["workJsonLd"]["data"]["work"]
        pd = self.get_work(work)

        manifestations = []
        for m in d["listOfAllManifestations"]["data"]["work"]["manifestations"][
            "mostRelevant"
        ]:
            if m["materialTypes"][0]["materialTypeSpecific"]["code"] == "BOOK":
                manifestations.append(m)

        editions = []
        for edition in manifestations:
            editionPd = BibliotekDK_Edition.get_edition(edition["pid"], d)
            data = {
                "model": "Edition",
                "id_type": IdType.BibliotekDK_Edition,
                "id_value": edition["pid"],
                "url": BibliotekDK_Edition.id_to_url(edition["pid"]),
                "content": {"metadata": editionPd.metadata},
            }

            if re.match(
                "https://moreinfo.addi.dk", editionPd.metadata["cover_image_url"]
            ):
                pd.metadata["cover_image_path"] = editionPd.metadata["cover_image_path"]

            editions.append(data)

        pd.metadata["required_resources"] = editions

        return pd
