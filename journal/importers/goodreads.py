import re
from datetime import datetime

from django.utils import timezone
from django.utils.timezone import make_aware

from catalog.common import *
from catalog.common.downloaders import *
from catalog.models import *
from journal.models import *
from users.models import Task

re_list = r"^https://www\.goodreads\.com/list/show/\d+"
re_shelf = r"^https://www\.goodreads\.com/review/list/\d+[^\?]*\?shelf=[^&]+"
re_profile = r"^https://www\.goodreads\.com/user/show/(\d+)"
gr_rating = {
    "did not like it": 2,
    "it was ok": 4,
    "liked it": 6,
    "really liked it": 8,
    "it was amazing": 10,
}


class GoodreadsImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "failed_urls": [],
        "url": None,
    }

    @classmethod
    def validate_url(cls, raw_url):
        match_list = re.match(re_list, raw_url)
        match_shelf = re.match(re_shelf, raw_url)
        match_profile = re.match(re_profile, raw_url)
        if match_profile or match_shelf or match_list:
            return True
        else:
            return False

    def run(self):
        url = self.metadata["url"]
        user = self.user
        match_list = re.match(re_list, url)
        match_shelf = re.match(re_shelf, url)
        match_profile = re.match(re_profile, url)
        total = 0
        visibility = user.preference.default_visibility
        shelf = None
        if match_shelf:
            shelf = self.parse_shelf(match_shelf[0])
        elif match_list:
            shelf = self.parse_list(match_list[0])
        if shelf:
            if shelf["title"] and shelf["books"]:
                collection = Collection.objects.create(
                    title=shelf["title"],
                    brief=shelf["description"]
                    + "\n\nImported from [Goodreads]("
                    + url
                    + ")",
                    owner=user.identity,
                )
                for book in shelf["books"]:
                    collection.append_item(book["book"], note=book["review"])
                    total += 1
                collection.save()
            self.message = f"Imported {total} books from Goodreads as a Collection {shelf['title']}."
        elif match_profile:
            uid = match_profile[1]
            shelves = {
                ShelfType.WISHLIST: f"https://www.goodreads.com/review/list/{uid}?shelf=to-read",
                ShelfType.PROGRESS: f"https://www.goodreads.com/review/list/{uid}?shelf=currently-reading",
                ShelfType.COMPLETE: f"https://www.goodreads.com/review/list/{uid}?shelf=read",
            }
            for shelf_type in shelves:
                shelf_url = shelves.get(shelf_type)
                shelf = self.parse_shelf(shelf_url)
                for book in shelf["books"]:
                    mark = Mark(user.identity, book["book"])
                    if (
                        (
                            mark.shelf_type == shelf_type
                            and mark.comment_text == book["review"]
                        )
                        or (
                            mark.shelf_type == ShelfType.COMPLETE
                            and shelf_type != ShelfType.COMPLETE
                        )
                        or (
                            mark.shelf_type == ShelfType.PROGRESS
                            and shelf_type == ShelfType.WISHLIST
                        )
                    ):
                        print(
                            f"Skip {shelf_type}/{book['book']} bc it was marked {mark.shelf_type}"
                        )
                    else:
                        mark.update(
                            shelf_type,
                            book["review"],
                            book["rating"],
                            visibility=visibility,
                            created_time=book["last_updated"] or timezone.now(),
                        )
                    total += 1
            self.message = f"Imported {total} records from Goodreads profile."
        self.metadata["total"] = total
        self.save()

    @classmethod
    def get_book(cls, url):
        site = SiteManager.get_site_by_url(url)
        if site:
            book = site.get_item()
            if not book:
                resource = site.get_resource_ready()
                if resource and resource.item:
                    book = resource.item
            return book

    @classmethod
    def parse_shelf(cls, url):
        # return {'title': 'abc', books: [{'book': obj, 'rating': 10, 'review': 'txt'}, ...]}
        title = ""
        books = []
        url_shelf = url + "&view=table"
        while url_shelf:
            print(f"Shelf loading {url_shelf}")
            try:
                content = BasicDownloader(url_shelf).download().html()
                title_elem = content.xpath("//span[@class='h1Shelf']/text()")
                if not title_elem:
                    print(f"Shelf parsing error {url_shelf}")
                    break
                title = title_elem[0].strip()  # type:ignore
                print(f"Shelf title: {title}")
            except Exception:
                print(f"Shelf loading/parsing error {url_shelf}")
                break
            cells = content.xpath("//tbody[@id='booksBody']/tr")
            for cell in cells:  # type:ignore
                url_book = (
                    "https://www.goodreads.com"
                    + cell.xpath(".//td[@class='field title']//a/@href")[0].strip()
                )
                # has_review = cell.xpath(
                #     ".//td[@class='field actions']//a/text()")[0].strip() == 'view (with text)'
                rating_elem = cell.xpath(".//td[@class='field rating']//span/@title")
                rating = gr_rating.get(rating_elem[0].strip()) if rating_elem else None
                url_review = (
                    "https://www.goodreads.com"
                    + cell.xpath(".//td[@class='field actions']//a/@href")[0].strip()
                )
                review = None
                last_updated = None
                date_elem = cell.xpath(".//td[@class='field date_added']//span/text()")
                for d in date_elem:
                    date_matched = re.search(r"(\w+)\s+(\d+),\s+(\d+)", d)
                    if date_matched:
                        last_updated = make_aware(
                            datetime.strptime(
                                date_matched[1]
                                + " "
                                + date_matched[2]
                                + " "
                                + date_matched[3],
                                "%b %d %Y",
                            )
                        )
                try:
                    c2 = BasicDownloader(url_review).download().html()
                    review_elem = c2.xpath("//div[@itemprop='reviewBody']/text()")
                    review = (
                        "\n".join(p.strip() for p in review_elem)  # type:ignore
                        if review_elem
                        else ""
                    )
                    date_elem = c2.xpath("//div[@class='readingTimeline__text']/text()")
                    for d in date_elem:  # type:ignore
                        date_matched = re.search(r"(\w+)\s+(\d+),\s+(\d+)", d)
                        if date_matched:
                            last_updated = make_aware(
                                datetime.strptime(
                                    date_matched[1]
                                    + " "
                                    + date_matched[2]
                                    + " "
                                    + date_matched[3],
                                    "%B %d %Y",
                                )
                            )
                except Exception:
                    print(f"Error loading/parsing review{url_review}, ignored")
                try:
                    book = cls.get_book(url_book)
                    books.append(
                        {
                            "url": url_book,
                            "book": book,
                            "rating": rating,
                            "review": review,
                            "last_updated": last_updated,
                        }
                    )
                except Exception as e:
                    print(f"Error adding {url_book} {e}")
                    pass  # likely just download error
            next_elem = content.xpath("//a[@class='next_page']/@href")
            url_shelf = (
                f"https://www.goodreads.com{next_elem[0].strip()}"  # type:ignore
                if next_elem
                else None
            )
        return {"title": title, "description": "", "books": books}

    @classmethod
    def parse_list(cls, url):
        # return {'title': 'abc', books: [{'book': obj, 'rating': 10, 'review': 'txt'}, ...]}
        title = ""
        description = ""
        books = []
        url_shelf = url
        while url_shelf:
            print(f"List loading {url_shelf}")
            content = BasicDownloader(url_shelf).download().html()
            title_elem = content.xpath('//h1[@class="gr-h1 gr-h1--serif"]/text()')
            if not title_elem:
                print(f"List parsing error {url_shelf}")
                break
            title: str = title_elem[0].strip()  # type:ignore
            desc_elem = content.xpath('//div[@class="mediumText"]/text()')
            description: str = desc_elem[0].strip()  # type:ignore
            print("List title: " + title)
            links = content.xpath('//a[@class="bookTitle"]/@href')
            for link in links:  # type:ignore
                url_book = "https://www.goodreads.com" + link
                try:
                    book = cls.get_book(url_book)
                    books.append(
                        {
                            "url": url_book,
                            "book": book,
                            "review": "",
                        }
                    )
                except Exception:
                    print("Error adding " + url_book)
                    pass  # likely just download error
            next_elem = content.xpath("//a[@class='next_page']/@href")
            url_shelf = (
                f"https://www.goodreads.com{next_elem[0].strip()}"  # type:ignore
                if next_elem
                else None
            )
        return {"title": title, "description": description, "books": books}
