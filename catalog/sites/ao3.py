from catalog.book.models import *
from catalog.common import *


@SiteManager.register
class ArchiveOfOurOwn(AbstractSite):
    SITE_NAME = SiteName.AO3
    ID_TYPE = IdType.AO3
    URL_PATTERNS = [
        r"\w+://archiveofourown\.org/works/(\d+)",
    ]
    WIKI_PROPERTY_ID = "?"
    DEFAULT_MODEL = Edition

    @classmethod
    def id_to_url(cls, id_value):
        return "https://archiveofourown.org/works/" + id_value

    def scrape(self):
        if not self.url:
            raise ParseError(self, "url")
        content = BasicDownloader(self.url + "?view_adult=true").download().html()

        title = content.xpath("string(//h2[@class='title heading'])")
        if not title:
            raise ParseError(self, "title")
        authors = content.xpath("//h3[@class='byline heading']/a/text()")
        summary = content.xpath(
            "string(//div[@class='summary module']//blockquote[@class='userstuff'])"
        )
        language = [
            s.strip()
            for s in content.xpath("//dd[@class='language']/text()")  # type:ignore
        ]

        published = content.xpath("string(//dd[@class='published']/text())")
        if published:
            pub_date = published.split("-")  # type:ignore
            pub_year = int(pub_date[0])
            pub_month = int(pub_date[1])
        else:
            pub_year = None
            pub_month = None
        data = {
            "localized_title": [{"lang": "en", "text": title.strip()}],  # type:ignore
            "localized_description": (
                [
                    {"lang": "en", "text": summary.strip()}  # type:ignore
                ]
                if summary
                else []
            ),
            "author": authors,
            "language": language,
            "pub_year": pub_year,
            "pub_month": pub_month,
            "format": Edition.BookFormat.WEB,
            # "words": words,
        }

        pd = ResourceContent(metadata=data)
        return pd
