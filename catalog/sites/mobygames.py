"""
MobyGames

Scrapes MobyGames game pages.
URL format: https://www.mobygames.com/game/{numeric_id}/{slug}/
Wikidata property P11688 maps to MobyGames numeric game IDs.
"""

import json

from catalog.common import *
from catalog.models import *
from catalog.sites.wikidata import WikiData
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

        h1_list = self.query_list(content, "//h1/text()")
        title = ld_json.get("name") or (h1_list[0].strip() if h1_list else "")
        if not title:
            raise ParseError(self, "title")

        # Build localized titles from alternate names in JSON-LD
        # alternateName can be a single string or a list per Schema.org spec
        localized_title = [{"lang": detect_language(title), "text": title}]
        alt_names = ld_json.get("alternateName") or []
        if isinstance(alt_names, str):
            alt_names = [alt_names]
        for alt in alt_names:
            if not any(e["text"] == alt for e in localized_title):
                localized_title.append({"lang": detect_language(alt), "text": alt})

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
        # Fall back to JSON-LD publisher list; publisher can be str, list of str,
        # or list of Organization objects per Schema.org spec
        if not publisher and ld_json.get("publisher"):
            pub_data = ld_json["publisher"]
            if not isinstance(pub_data, list):
                pub_data = [pub_data]
            publisher = [p["name"] if isinstance(p, dict) else p for p in pub_data]

        # Genre from link text inside the Genre dt/dd pair
        genre = [
            g.strip()
            for g in self.query_list(
                content,
                '//dt[normalize-space(text())="Genre"]/following-sibling::dd[1]//a/text()',
            )
            if g.strip()
        ]

        # Platforms from JSON-LD (most complete source); can be a single string
        platform = ld_json.get("gamePlatform") or []
        if isinstance(platform, str):
            platform = [platform]

        # Release date from JSON-LD
        release_date = ld_json.get("datePublished") or None

        # Official site URL
        official_sites = self.query_list(
            content,
            '//dt[normalize-space(text())="Official Site"]/following-sibling::dd[1]//a/@href',
        )
        official_site = official_sites[0] if official_sites else None

        # Cover image: image can be a URL string or an ImageObject per Schema.org
        img_data = ld_json.get("image")
        cover_image_url = (
            img_data.get("url") if isinstance(img_data, dict) else img_data
        ) or None
        if not cover_image_url:
            og_image = self.query_list(content, '//meta[@property="og:image"]/@content')
            cover_image_url = og_image[0] if og_image else None

        pd = ResourceContent(
            metadata={
                "title": title,
                "localized_title": localized_title,
                "brief": brief,
                "localized_description": [
                    {"lang": detect_language(brief), "text": brief}
                ]
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
        # Look up the Wikidata QID for this MobyGames ID so the deduplication
        # flow can match against items already imported from Steam, IGDB, etc.
        if self.id_value:
            qid = WikiData.lookup_qid_by_external_id(IdType.MobyGames, self.id_value)
            if qid:
                pd.lookup_ids[IdType.WikiData] = qid
        return pd
