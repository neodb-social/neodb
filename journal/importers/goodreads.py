import re
from datetime import datetime

import django_rq
from auditlog.context import set_actor
from django.utils.timezone import make_aware
from user_messages import api as msg

from catalog.common import *
from catalog.common.downloaders import *
from catalog.models import *
from journal.models import *

re_list = r"^https://www.goodreads.com/list/show/\d+"
re_shelf = r"^https://www.goodreads.com/review/list/\d+[^?]*\?shelf=[^&]+"
re_profile = r"^https://www.goodreads.com/user/show/(\d+)"
gr_rating = {
    "did not like it": 2,
    "it was ok": 4,
    "liked it": 6,
    "really liked it": 8,
    "it was amazing": 10,
}


class GoodreadsImporter:
    @classmethod
    def import_from_url(cls, raw_url, user):
        match_list = re.match(re_list, raw_url)
        match_shelf = re.match(re_shelf, raw_url)
        match_profile = re.match(re_profile, raw_url)
        if match_profile or match_shelf or match_list:
            django_rq.get_queue("import").enqueue(
                cls.import_from_url_task, raw_url, user
            )
            return True
        else:
            return False

    @classmethod
    def import_from_url_task(cls, url, user):
        match_list = re.match(re_list, url)
        match_shelf = re.match(re_shelf, url)
        match_profile = re.match(re_profile, url)
        total = 0
        visibility = user.preference.default_visibility
        with set_actor(user):
            if match_list or match_shelf:
                shelf = (
                    cls.parse_shelf(match_shelf[0], user)
                    if match_shelf
                    else cls.parse_list(match_list[0], user)
                )
                if shelf["title"] and shelf["books"]:
                    collection = Collection.objects.create(
                        title=shelf["title"],
                        brief=shelf["description"]
                        + "\n\nImported from [Goodreads]("
                        + url
                        + ")",
                        owner=user,
                    )
                    for book in shelf["books"]:
                        collection.append_item(book["book"], note=book["review"])
                        total += 1
                    collection.save()
                msg.success(user, f'成功从Goodreads导入包含{total}本书的收藏单{shelf["title"]}。')
            elif match_profile:
                uid = match_profile[1]
                shelves = {
                    ShelfType.WISHLIST: f"https://www.goodreads.com/review/list/{uid}?shelf=to-read",
                    ShelfType.PROGRESS: f"https://www.goodreads.com/review/list/{uid}?shelf=currently-reading",
                    ShelfType.COMPLETE: f"https://www.goodreads.com/review/list/{uid}?shelf=read",
                }
                for shelf_type in shelves:
                    shelf_url = shelves.get(shelf_type)
                    shelf = cls.parse_shelf(shelf_url, user)
                    for book in shelf["books"]:
                        mark = Mark(user, book["book"])
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
                                f'Skip {shelf_type}/{book["book"]} bc it was marked {mark.shelf_type}'
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
                msg.success(user, f"成功从Goodreads用户主页导入{total}个标记。")

    @classmethod
    def get_book(cls, url, user):
        site = SiteManager.get_site_by_url(url)
        if site:
            book = site.get_item()
            if not book:
                resource = site.get_resource_ready()
                if resource and resource.item:
                    book = resource.item
            return book

    @classmethod
    def parse_shelf(cls, url, user):
        # return {'title': 'abc', books: [{'book': obj, 'rating': 10, 'review': 'txt'}, ...]}
        title = None
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
                print("Shelf title: " + title)
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
                    book = cls.get_book(url_book, user)
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
                f"https://www.goodreads.com{next_elem[0].strip()}"
                if next_elem
                else None
            )
        return {"title": title, "description": "", "books": books}

    @classmethod
    def parse_list(cls, url, user):
        # return {'title': 'abc', books: [{'book': obj, 'rating': 10, 'review': 'txt'}, ...]}
        title = None
        description = None
        books = []
        url_shelf = url
        while url_shelf:
            print(f"List loading {url_shelf}")
            content = BasicDownloader(url_shelf).download().html()
            title_elem = content.xpath('//h1[@class="gr-h1 gr-h1--serif"]/text()')
            if not title_elem:
                print(f"List parsing error {url_shelf}")
                break
            title = title_elem[0].strip()
            description = content.xpath('//div[@class="mediumText"]/text()')[0].strip()
            print("List title: " + title)
            for link in content.xpath('//a[@class="bookTitle"]/@href'):
                url_book = "https://www.goodreads.com" + link
                try:
                    book = cls.get_book(url_book, user)
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
                ("https://www.goodreads.com" + next_elem[0].strip())
                if next_elem
                else None
            )
        return {"title": title, "description": description, "books": books}
