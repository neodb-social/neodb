import re
from functools import cached_property
from typing import override

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
    r"^\s*(?P<prefix>(p|pg|page|ch|chapter|pt|part|e|ep|episode|trk|track|cycle))?(\s|\.|#)*(?P<value>(\d[\d\:\.\-]*\d|\d))\s*(?P<postfix>(%))?\s*(\s|\n|\.|:)",
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
        return self.progress_type.display + ": " + self.progress_value

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
        params = {
            "title": obj.get("title", post.summary),
            "content": obj.get("content", "").strip(),
            "sensitive": obj.get("sensitive", post.sensitive),
            "attachments": [],
        }
        progress = obj.get("progress", {})
        if progress.get("type"):
            params["progress_type"] = progress.get("type")
        if progress.get("value"):
            params["progress_value"] = progress.get("value")
        if post.local:
            progress_type, progress_value = cls.extract_progress(params["content"])
            print(progress_type, progress_value)
            if progress_value:
                params["progress_type"] = progress_type
                params["progress_value"] = progress_value
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
        p = super().update_by_ap_object(owner, item, obj, post)
        if (
            p
            and p.local
            and owner.user.preference.mastodon_default_repost
            and owner.user.mastodon_username
        ):
            p.sync_to_mastodon()
        return p

    @cached_property
    def shelfmember(self) -> ShelfMember | None:
        return ShelfMember.objects.filter(item=self.item, owner=self.owner).first()

    def to_mastodon_params(self):
        params = {
            "spoiler_text": self.title,
            "content": self.content,
            "sensitive": self.sensitive,
            "reply_to_toot_url": (
                self.shelfmember.get_mastodon_repost_url() if self.shelfmember else None
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
        return {
            "summary": self.title,
            "content": self.content,
            "sensitive": self.sensitive,
            "reply_to_pk": (
                self.shelfmember.latest_post_id if self.shelfmember else None
            ),
            # not passing "attachments" so it won't change
        }

    @classmethod
    def extract_progress(cls, content):
        m = _progress.match(content)
        if m and m["value"]:
            typ_ = "percentage" if m["postfix"] == "%" else m["prefix"]
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
