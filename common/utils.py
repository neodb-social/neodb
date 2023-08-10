import uuid

from django.http import Http404
from django.utils import timezone
from django.utils.baseconv import base62


class PageLinksGenerator:
    # TODO inherit django paginator
    """
    Calculate the pages for multiple links pagination.
    length -- the number of page links in pagination
    """

    def __init__(self, length: int, current_page: int, total_pages: int):
        current_page = int(current_page)
        self.current_page = current_page
        self.previous_page = current_page - 1 if current_page > 1 else None
        self.next_page = current_page + 1 if current_page < total_pages else None
        self.start_page = 1
        self.end_page = 1
        self.page_range = None
        self.has_prev = None
        self.has_next = None

        start_page = current_page - length // 2
        end_page = current_page + length // 2

        # decision is based on the start page and the end page
        # both sides overflow
        if (start_page < 1 and end_page > total_pages) or length >= total_pages:
            self.start_page = 1
            self.end_page = total_pages
            self.has_prev = False
            self.has_next = False

        elif start_page < 1 and not end_page > total_pages:
            self.start_page = 1
            # this won't overflow because the total pages are more than the length
            self.end_page = end_page - (start_page - 1)
            self.has_prev = False
            if end_page == total_pages:
                self.has_next = False
            else:
                self.has_next = True

        elif not start_page < 1 and end_page > total_pages:
            self.end_page = total_pages
            self.start_page = start_page - (end_page - total_pages)
            self.has_next = False
            if start_page == 1:
                self.has_prev = False
            else:
                self.has_prev = True

        # both sides do not overflow
        elif not start_page < 1 and not end_page > total_pages:
            self.start_page = start_page
            self.end_page = end_page
            self.has_prev = True
            self.has_next = True

        self.first_page = 1
        self.last_page = total_pages
        self.page_range = range(self.start_page, self.end_page + 1)
        # assert self.has_prev is not None and self.has_next is not None


def GenerateDateUUIDMediaFilePath(instance, filename, path_root):
    ext = filename.split(".")[-1]
    filename = "%s.%s" % (uuid.uuid4(), ext)
    root = ""
    if path_root.endswith("/"):
        root = path_root
    else:
        root = path_root + "/"
    return root + timezone.now().strftime("%Y/%m/%d") + f"{filename}"


def get_uuid_or_404(uuid_b62):
    try:
        i = base62.decode(uuid_b62)
        return uuid.UUID(int=i)
    except ValueError:
        raise Http404("Malformed Base62 UUID")
