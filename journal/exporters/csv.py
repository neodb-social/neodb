import csv
import os
import shutil
import tempfile

from django.conf import settings

from catalog.common.models import Item
from catalog.models import ItemCategory
from common.utils import GenerateDateUUIDMediaFilePath
from journal.models import Note, Review, ShelfMember, q_item_in_category
from users.models import Task

#

_mark_heading = [
    "title",
    "info",
    "links",
    "timestamp",
    "status",
    "rating",
    "comment",
    "tags",
]

_review_heading = [
    "title",
    "info",
    "links",
    "timestamp",
    "title",
    "content",
]

_note_heading = [
    "title",
    "info",
    "links",
    "timestamp",
    "progress",
    "title",
    "content",
]


class CsvExporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "export"
    DefaultMetadata = {
        "file": None,
        "total": 0,
    }

    @property
    def filename(self) -> str:
        d = self.created_time.strftime("%Y%m%d%H%M%S")
        return f"neodb_{self.user.username}_{d}_csv"

    def get_item_info(self, item: Item) -> str:
        s = []
        for a in [
            "imdb",
            "isbn",
            "year",
            "pub_year",
            "season_number",
            "episode_number",
        ]:
            if hasattr(item, a) and getattr(item, a):
                s.append(f"{a}:{getattr(item, a)}")
        for a in ["author", "artist", "director", "host"]:
            if hasattr(item, a) and getattr(item, a):
                s.append(f"{a}:{'/'.join(getattr(item, a))}")
        return " ".join(s)

    def get_item_links(self, item: Item) -> str:
        links = [item.absolute_url]
        for ext in item.external_resources.all():
            links.append(ext.url)
        return " ".join(links)

    def run(self):
        user = self.user
        temp_dir = tempfile.mkdtemp()
        temp_folder_path = os.path.join(temp_dir, self.filename)
        os.makedirs(temp_folder_path)
        total = 0
        for category in [
            ItemCategory.Movie,
            ItemCategory.TV,
            ItemCategory.Music,
            ItemCategory.Book,
            ItemCategory.Game,
            ItemCategory.Podcast,
            ItemCategory.Performance,
        ]:
            q = q_item_in_category(category)
            csv_file_path = os.path.join(temp_folder_path, f"{category}")
            marks = (
                ShelfMember.objects.filter(owner=user.identity)
                .filter(q)
                .order_by("created_time")
            )
            with open(csv_file_path + "_mark.csv", "w") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(_mark_heading)
                for mark in marks:
                    total += 1
                    item = mark.item
                    line = [
                        item.display_title,
                        self.get_item_info(item),
                        self.get_item_links(item),
                        mark.created_time.isoformat(),
                        mark.shelf_type,
                        mark.rating_grade,
                        mark.comment_text,
                        " ".join(mark.tags),
                    ]
                    writer.writerow(line)
            reviews = (
                Review.objects.filter(owner=user.identity)
                .filter(q)
                .order_by("created_time")
            )
            with open(csv_file_path + "_review.csv", "w") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(_review_heading)
                for review in reviews:
                    total += 1
                    item = review.item
                    line = [
                        item.display_title,
                        self.get_item_info(item),
                        self.get_item_links(item),
                        review.created_time.isoformat(),
                        review.title,
                        review.body,
                    ]
                    writer.writerow(line)
            with open(csv_file_path + "_note.csv", "w") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(_note_heading)
                notes = (
                    Note.objects.filter(owner=user.identity)
                    .filter(q)
                    .order_by("created_time")
                )
                for note in notes:
                    total += 1
                    item = note.item
                    line = [
                        item.display_title,
                        self.get_item_info(item),
                        self.get_item_links(item),
                        note.created_time.isoformat(),
                        note.progress_display,
                        note.title,
                        note.content,
                    ]
                    writer.writerow(line)

        filename = GenerateDateUUIDMediaFilePath(
            "f.zip", settings.MEDIA_ROOT + "/" + settings.EXPORT_FILE_PATH_ROOT
        )
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))
        shutil.make_archive(filename[:-4], "zip", temp_folder_path)
        self.metadata["file"] = filename
        self.metadata["total"] = total
        self.message = "Export complete."
        self.save()
