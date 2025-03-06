import json
import os
import re
import shutil
import tempfile

from django.conf import settings
from django.utils import timezone
from loguru import logger

from catalog.common import ProxiedImageDownloader
from common.utils import GenerateDateUUIDMediaFilePath
from journal.models import (
    Collection,
    Content,
    Note,
    Review,
    ShelfLogEntry,
    ShelfMember,
    Tag,
    TagMember,
)
from takahe.models import Post
from users.models import Task


class NdjsonExporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "export"
    DefaultMetadata = {
        "file": None,
        "total": 0,
    }
    ref_items = []

    @property
    def filename(self) -> str:
        d = self.created_time.strftime("%Y%m%d%H%M%S")
        return f"neodb_{self.user.username}_{d}_ndjson"

    def ref(self, item) -> str:
        if item not in self.ref_items:
            self.ref_items.append(item)
        return item.absolute_url

    def get_header(self):
        return {
            "server": settings.SITE_DOMAIN,
            "neodb_version": settings.NEODB_VERSION,
            "username": self.user.username,
            "actor": self.user.identity.actor_uri,
            "request_time": self.created_time.isoformat(),
            "created_time": timezone.now().isoformat(),
        }

    def run(self):
        user = self.user
        temp_dir = tempfile.mkdtemp()
        temp_folder_path = os.path.join(temp_dir, self.filename)
        os.makedirs(temp_folder_path)
        attachment_path = os.path.join(temp_folder_path, "attachments")
        os.makedirs(attachment_path, exist_ok=True)

        def _save_image(url):
            if url.startswith("http"):
                imgdl = ProxiedImageDownloader(url)
                raw_img = imgdl.download().content
                ext = imgdl.extention
                file = GenerateDateUUIDMediaFilePath(f"x.{ext}", attachment_path)
                with open(file, "wb") as binary_file:
                    binary_file.write(raw_img)
                return file
            elif url.startswith("/"):
                p = os.path.abspath(
                    os.path.join(settings.MEDIA_ROOT, url[len(settings.MEDIA_URL) :])
                )
                if p.startswith(settings.MEDIA_ROOT):
                    try:
                        shutil.copy2(p, attachment_path)
                    except Exception as e:
                        logger.error(
                            f"error copying {p} to {attachment_path}",
                            extra={"exception": e},
                        )
                return p
            return url

        filename = os.path.join(temp_folder_path, "journal.ndjson")
        total = 0
        with open(filename, "w") as f:
            f.write(json.dumps(self.get_header()) + "\n")

            for cls in list(Content.__subclasses__()):
                pieces = cls.objects.filter(owner=user.identity)
                for p in pieces:
                    total += 1
                    self.ref(p.item)
                    o = {
                        "type": p.__class__.__name__,
                        "content": p.ap_object,
                        "visibility": p.visibility,
                        "metadata": p.metadata,
                    }
                    f.write(json.dumps(o, default=str) + "\n")
                    if cls == Review:
                        re.sub(
                            r"(?<=!\[\]\()([^)]+)(?=\))",
                            lambda x: _save_image(x[1]),
                            p.body,  # type: ignore
                        )
                    elif cls == Note and p.latest_post:
                        for a in p.latest_post.attachments.all():
                            dest = os.path.join(
                                attachment_path, os.path.basename(a.file.name)
                            )
                            try:
                                shutil.copy2(a.file.path, dest)
                            except Exception as e:
                                logger.error(
                                    f"error copying {a.file.path} to {dest}",
                                    extra={"exception": e},
                                )

            collections = Collection.objects.filter(owner=user.identity)
            for c in collections:
                total += 1
                o = {
                    "type": "Collection",
                    "content": c.ap_object,
                    "visibility": c.visibility,
                    "metadata": c.metadata,
                    "items": [
                        {"item": self.ref(m.item), "metadata": m.metadata}
                        for m in c.ordered_members
                    ],
                }
                f.write(json.dumps(o, default=str) + "\n")

            tags = Tag.objects.filter(owner=user.identity)
            for t in tags:
                total += 1
                o = {
                    "type": "Tag",
                    "name": t.title,
                    "visibility": t.visibility,
                    "pinned": t.pinned,
                }
                f.write(json.dumps(o, default=str) + "\n")

            tags = TagMember.objects.filter(owner=user.identity)
            for t in tags:
                total += 1
                o = {
                    "type": "TagMember",
                    "content": t.ap_object,
                    "visibility": t.visibility,
                    "metadata": t.metadata,
                }
                f.write(json.dumps(o, default=str) + "\n")
            marks = ShelfMember.objects.filter(owner=user.identity)
            for m in marks:
                total += 1
                o = {
                    "type": "ShelfMember",
                    "content": m.ap_object,
                    "visibility": m.visibility,
                    "metadata": m.metadata,
                }
                f.write(json.dumps(o, default=str) + "\n")

            logs = ShelfLogEntry.objects.filter(owner=user.identity)
            for log in logs:
                total += 1
                o = {
                    "type": "ShelfLog",
                    "item": self.ref(log.item),
                    "status": log.shelf_type,
                    "posts": list(log.all_post_ids()),
                    "timestamp": log.timestamp,
                }
                f.write(json.dumps(o, default=str) + "\n")

            posts = Post.objects.filter(author_id=user.identity.pk).exclude(
                type_data__has_key="object"
            )

            for p in posts:
                total += 1
                o = {"type": "post", "post": p.to_mastodon_json()}
                for a in p.attachments.all():
                    dest = os.path.join(attachment_path, os.path.basename(a.file.name))
                    try:
                        shutil.copy2(a.file.path, dest)
                    except Exception as e:
                        logger.error(
                            f"error copying {a.file.path} to {dest}",
                            extra={"exception": e},
                        )
                f.write(json.dumps(o, default=str) + "\n")

        filename = os.path.join(temp_folder_path, "catalog.ndjson")
        with open(filename, "w") as f:
            f.write(json.dumps(self.get_header()) + "\n")
            for item in self.ref_items:
                f.write(json.dumps(item.ap_object, default=str) + "\n")

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
