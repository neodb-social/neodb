import re
from functools import cached_property
from typing import override

from deepmerge import always_merger
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import Item
from mastodon.api import delete_toot_later
from takahe.utils import Takahe

from .common import Content
from .renderers import render_text
from .shelf import ShelfMember

_progress = re.compile(
    r"(.*\s)?(?P<prefix>(p|pg|page|ch|chapter|pt|part|e|ep|episode|trk|track|cycle))(\s|\.|#)*(?P<value>(\d[\d\:\.\-]*\d|\d))\s*(?P<postfix>(%))?(\s|\n|\.|。)?$",
    re.IGNORECASE,
)

_progress2 = re.compile(
    r"(.*\s)?(?P<value>(\d[\d\:\.\-]*\d|\d))\s*(?P<postfix>(%))?(\s|\n|\.|。)?$",
    re.IGNORECASE,
)

_number = re.compile(r"^[\s\d\:\.]+$")


class Note(Content):
    class ProgressType(models.TextChoices):
        PAGE = "page", _("Page")
        CHAPTER = "chapter", _("Chapter")
        # SECTION = "section", _("Section")
        # VOLUME = "volume", _("Volume")
        PART = "part", _("Part")
        EPISODE = "episode", _("Episode")
        TRACK = "track", _("Track")
        CYCLE = "cycle", _("Cycle")
        TIMESTAMP = "timestamp", _("Timestamp")
        PERCENTAGE = "percentage", _("Percentage")

    title = models.TextField(blank=True, null=True, default=None)
    content = models.TextField(blank=False, null=False)
    sensitive = models.BooleanField(default=False, null=False)
    attachments = models.JSONField(default=list)
    progress_type = models.CharField(
        max_length=50,
        choices=ProgressType.choices,
        blank=True,
        null=True,
        default=None,
    )
    progress_value = models.CharField(
        max_length=500, blank=True, null=True, default=None
    )
    _progress_display_template = {
        ProgressType.PAGE: _("Page {value}"),
        ProgressType.CHAPTER: _("Chapter {value}"),
        # ProgressType.SECTION: _("Section {value}"),
        # ProgressType.VOLUME: _("Volume {value}"),
        ProgressType.PART: _("Part {value}"),
        ProgressType.EPISODE: _("Episode {value}"),
        ProgressType.TRACK: _("Track {value}"),
        ProgressType.CYCLE: _("Cycle {value}"),
        ProgressType.PERCENTAGE: "{value}%",
        ProgressType.TIMESTAMP: "{value}",
    }

    class Meta:
        indexes = [models.Index(fields=["owner", "item", "created_time"])]

    @property
    def html(self):
        return render_text(self.content)

    @property
    def progress_display(self) -> str:
        if not self.progress_value:
            return ""
        if not self.progress_type:
            return str(self.progress_value)
        tpl = Note._progress_display_template.get(self.progress_type, None)
        if not tpl:
            return str(self.progress_value)
        if _number.match(self.progress_value):
            return tpl.format(value=self.progress_value)
        return Note.ProgressType(self.progress_type).label + ": " + self.progress_value

    @property
    def ap_object(self):
        d = {
            "id": self.absolute_url,
            "type": "Note",
            "title": self.title,
            "content": self.content,
            "sensitive": self.sensitive,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "href": self.absolute_url,
        }
        if self.progress_value:
            d["progress"] = {
                "type": self.progress_type,
                "value": self.progress_value,
            }
        return d

    @override
    @classmethod
    def params_from_ap_object(cls, post, obj, piece):
        content = obj.get("content", "").strip()
        footer = []
        if post.local:
            # strip footer from local post if detected
            lines = content.splitlines()
            if len(lines) > 2 and lines[-2].strip() in ["—", "-"]:
                content = "\n".join(lines[:-2])
                footer = lines[-2:]
        params = {
            "title": obj.get("title", post.summary),
            "content": content,
            "sensitive": obj.get("sensitive", post.sensitive),
            "attachments": [],
        }
        progress = obj.get("progress", {})
        if progress.get("type"):
            params["progress_type"] = progress.get("type")
        if progress.get("value"):
            params["progress_value"] = progress.get("value")
        if post.local and len(footer) == 2:
            progress_type, progress_value = cls.extract_progress(footer[1])
            if progress_value:
                params["progress_type"] = progress_type
                params["progress_value"] = progress_value
            elif not footer[1].startswith("https://"):
                # add footer back if unable to regconize correct patterns
                params["content"] += "\n" + "\n".join(footer)
        if post:
            for atta in post.attachments.all():
                params["attachments"].append(
                    {
                        "type": (atta.mimetype or "unknown").split("/")[0],
                        "mimetype": atta.mimetype,
                        "url": atta.full_url().absolute,
                        "preview_url": atta.thumbnail_url().absolute,
                    }
                )
        return params

    @override
    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post):
        # new_piece = cls.get_by_post_id(post.id) is None
        p = super().update_by_ap_object(owner, item, obj, post)
        if p and p.local:
            # if local piece is created from a post, update post type_data and fanout
            p.sync_to_timeline()
            if (
                owner.user.preference.mastodon_default_repost
                and owner.user.mastodon_username
            ):
                p.sync_to_mastodon()
        return p

    @cached_property
    def shelfmember(self) -> ShelfMember | None:
        return ShelfMember.objects.filter(item=self.item, owner=self.owner).first()

    def to_mastodon_params(self):
        footer = f"\n—\n《{self.item.display_title}》 {self.progress_display}\n{self.item.absolute_url}"
        params = {
            "spoiler_text": self.title,
            "content": self.content + footer,
            "sensitive": self.sensitive,
            "reply_to_toot_url": (
                self.shelfmember.get_mastodon_crosspost_url()
                if self.shelfmember
                else None
            ),
        }
        if self.latest_post:
            attachments = []
            for atta in self.latest_post.attachments.all():
                attachments.append((atta.file_display_name, atta.file, atta.mimetype))
            if attachments:
                params["attachments"] = attachments
        return params

    def to_post_params(self):
        footer = f'\n<p>—<br><a href="{self.item.absolute_url}">{self.item.display_title}</a> {self.progress_display}\n</p>'
        post = self.shelfmember.latest_post if self.shelfmember else None
        return {
            "summary": self.title,
            "content": self.content,
            "append_content": footer,
            "sensitive": self.sensitive,
            "reply_to_pk": post.pk if post else None,
            # not passing "attachments" so it won't change
        }

    @classmethod
    def extract_progress(cls, content):
        m = _progress.match(content)
        if not m:
            m = _progress2.match(content)
        if m and m["value"]:
            m = m.groupdict()
            typ_ = "percentage" if m["postfix"] == "%" else m.get("prefix", "")
            match typ_:
                case "p" | "pg" | "page":
                    typ = Note.ProgressType.PAGE
                case "ch" | "chapter":
                    typ = Note.ProgressType.CHAPTER
                # case "vol" | "volume":
                #     typ = ProgressType.VOLUME
                # case "section":
                #     typ = ProgressType.SECTION
                case "pt" | "part":
                    typ = Note.ProgressType.PART
                case "e" | "ep" | "episode":
                    typ = Note.ProgressType.EPISODE
                case "trk" | "track":
                    typ = Note.ProgressType.TRACK
                case "cycle":
                    typ = Note.ProgressType.CYCLE
                case "percentage":
                    typ = Note.ProgressType.PERCENTAGE
                case _:
                    typ = "timestamp" if ":" in m["value"] else None
            return typ, m["value"]
        return None, None

    @classmethod
    def get_progress_types_by_item(cls, item: Item):
        match item.__class__.__name__:
            case "Edition":
                v = [
                    Note.ProgressType.PAGE,
                    Note.ProgressType.CHAPTER,
                    Note.ProgressType.PERCENTAGE,
                ]
            case "TVShow" | "TVSeason":
                v = [
                    Note.ProgressType.PART,
                    Note.ProgressType.EPISODE,
                    Note.ProgressType.PERCENTAGE,
                ]
            case "Movie":
                v = [
                    Note.ProgressType.PART,
                    Note.ProgressType.TIMESTAMP,
                    Note.ProgressType.PERCENTAGE,
                ]
            case "Podcast":
                v = [
                    Note.ProgressType.EPISODE,
                ]
            case "TVEpisode" | "PodcastEpisode":
                v = []
            case "Album":
                v = [
                    Note.ProgressType.TRACK,
                    Note.ProgressType.TIMESTAMP,
                    Note.ProgressType.PERCENTAGE,
                ]
            case "Game":
                v = [
                    Note.ProgressType.CYCLE,
                ]
            case "Performance" | "PerformanceProduction":
                v = [
                    Note.ProgressType.PART,
                    Note.ProgressType.TIMESTAMP,
                    Note.ProgressType.PERCENTAGE,
                ]
            case _:
                v = []
        return v
