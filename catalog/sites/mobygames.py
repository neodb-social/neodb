"""
MobyGames

Scrapes MobyGames game pages.
URL format: https://www.mobygames.com/game/{numeric_id}/{slug}/
Wikidata property P11688 maps to MobyGames numeric game IDs.
"""

import json

from catalog.common import *
from catalog.models import *
from common.models.lang import detect_language


@SiteManager.register
class MobyGames(AbstractSite):
    SITE_NAME = SiteName.MobyGames
    ID_TYPE = IdType.MobyGames
    URL_PATTERNS = [
        r"\w+://www\.mobygames\.com/game/(\d+)[/\w\-]*",
    ]
    WIKI_PROPERTY_ID = "P11688"
    DEFAULT_MODEL = Game

    @classmethod
    def id_to_url(cls, id_value):
        return "https://www.mobygames.com/game/" + id_value + "/"

    def scrape(self):
        if not self.url:
            raise ParseError(self, "url")
        content = BasicDownloader(self.url).download().html()

        # Parse JSON-LD structured data
        ld_json = {}
        ld_src = self.query_list(
            content, '//script[@type="application/ld+json"]/text()'
        )
        if ld_src:
            try:
                ld_json = json.loads(ld_src[0])
            except (json.JSONDecodeError, ValueError):
                pass

        title = ld_json.get("name") or self.query_str(content, "//h1/text()").strip()
        if not title:
            raise ParseError(self, "title")

        # Build localized titles from alternate names in JSON-LD
        localized_title = [{"lang": detect_language(title), "text": title}]
        for alt in ld_json.get("alternateName") or []:
            lang = detect_language(alt)
            if not any(e["text"] == alt for e in localized_title):
                localized_title.append({"lang": lang, "text": alt})

        # Description from the in-page description block (preferred) or meta tag
        desc_paras = self.query_list(content, '//div[@id="description-text"]//p')
        if desc_paras:
            brief = "\n\n".join(
                p.text_content().strip() for p in desc_paras if p.text_content().strip()
            )
        else:
            brief_list = self.query_list(
                content, '//meta[@name="description"]/@content'
            )
            brief = brief_list[0] if brief_list else ""

        # Developer and publisher from the HTML link lists
        developer = [
            d.strip()
            for d in self.query_list(content, '//ul[@id="developerLinks"]//a/text()')
            if d.strip()
        ]
        publisher = [
            p.strip()
            for p in self.query_list(content, '//ul[@id="publisherLinks"]//a/text()')
            if p.strip()
        ]
        # Fall back to JSON-LD publisher list
        if not publisher and ld_json.get("publisher"):
            publisher = ld_json["publisher"]

        # Genre from link text inside the Genre dt/dd pair
        genre = [
            g.strip()
            for g in self.query_list(
                content,
                '//dt[normalize-space(text())="Genre"]/following-sibling::dd[1]//a/text()',
            )
            if g.strip()
        ]

        # Platforms from JSON-LD (most complete source)
        platform = ld_json.get("gamePlatform") or []

        # Release date from JSON-LD
        release_date = ld_json.get("datePublished") or None

        # Official site URL
        official_sites = self.query_list(
            content,
            '//dt[normalize-space(text())="Official Site"]/following-sibling::dd[1]//a/@href',
        )
        official_site = official_sites[0] if official_sites else None

        # Cover image from og:image meta tag
        cover_image_url = ld_json.get("image") or None
        if not cover_image_url:
            og_image = self.query_list(content, '//meta[@property="og:image"]/@content')
            cover_image_url = og_image[0] if og_image else None

        return ResourceContent(
            metadata={
                "title": title,
                "localized_title": localized_title,
                "brief": brief,
                "localized_description": [{"lang": "en", "text": brief}]
                if brief
                else [],
                "developer": developer,
                "publisher": publisher,
                "genre": genre,
                "platform": platform,
                "release_date": release_date,
                "official_site": official_site,
                "cover_image_url": cover_image_url,
            }
        )
