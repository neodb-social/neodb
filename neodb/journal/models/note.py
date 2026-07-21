import re
from functools import cached_property
from typing import Any, override

from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.models import Item

from .common import Content
from .renderers import render_text
from .shelf import ShelfMember

_progress = re.compile(
    r"(.*\s)?(?P<prefix>(p|pg|page|ch|chapter|pt|part|e|ep|episode|trk|track|cycle))(\s|\.|#)*(?P<value>([\d\:\.\-]+))\s*(?P<postfix>(%))?(\s|\n|\.|。)?$",
    re.IGNORECASE,
)

_progress2 = re.compile(
    r"(.*\s)?(?P<value>([\d\:\.\-]+))\s*(?P<postfix>(%))?(\s|\n|\.|。)?$",
    re.IGNORECASE,
)

_number = re.compile(r"^[\s\d\:\.]+$")

_separaters = {"–", "―", "−", "—", "-"}


class Note(Content):
    post_when_save = True
    index_when_save = True

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
    _progress_short_display_template = {
        ProgressType.PAGE: "p{value}",
        ProgressType.CHAPTER: "ch{value}",
        ProgressType.PART: "pt{value}",
        ProgressType.EPISODE: "ep{value}",
        ProgressType.TRACK: "trk{value}",
        ProgressType.CYCLE: "cycle {value}",
        ProgressType.PERCENTAGE: "{value}%",
        ProgressType.TIMESTAMP: "{value}",
    }

    class Meta:
        indexes = [
            models.Index(fields=["owner", "item", "created_time"]),
            models.Index(fields=["remote_id"], name="note_remote_id_idx"),
        ]

    @property
    def html(self):
        return render_text(self.content)

    @property
    def progress_display(self) -> str:
        return self.format_progress(self.progress_type, self.progress_value)

    @classmethod
    def format_progress(
        cls, progress_type: str | None, progress_value: str | None
    ) -> str:
        if not progress_value:
            return ""
        if not progress_type:
            return str(progress_value)
        tpl = cls._progress_display_template.get(progress_type, None)
        if not tpl:
            return str(progress_value)
        if _number.match(progress_value):
            return tpl.format(value=progress_value)
        return cls.ProgressType(progress_type).label + ": " + progress_value

    @classmethod
    def format_progress_short(
        cls, progress_type: str | None, progress_value: str | None
    ) -> str:
        if not progress_value:
            return ""
        if not progress_type:
            return str(progress_value)
        tpl = cls._progress_short_display_template.get(progress_type)
        if not tpl:
            return str(progress_value)
        return tpl.format(value=progress_value)

    @classmethod
    def get_progress_percentage(
        cls,
        progress_type: str | None,
        progress_value: str | None,
        total: int | None = None,
    ) -> int | None:
        """Return reading progress as an integer percent (0-100), if derivable.

        Percentage-type progress is used as-is; page-type is converted when a
        positive total page count is known. Other types, and pages without a
        usable total, have no derivable percentage and return None.

        Inputs are coerced defensively so a bad value never raises: the value
        is free-text, and ``total`` comes from an item's metadata JSON where a
        page count may be stored as a non-int (or be negative/zero). Anything
        that cannot be parsed into a positive total simply yields None.
        """
        if not progress_value or not _number.match(str(progress_value)):
            return None
        try:
            value = float(str(progress_value))
        except TypeError, ValueError:
            return None
        if progress_type == cls.ProgressType.PERCENTAGE:
            percent = value
        elif progress_type == cls.ProgressType.PAGE:
            if total is None:
                return None
            try:
                total_pages = float(total)
            except TypeError, ValueError:
                return None
            if total_pages <= 0:
                return None
            percent = value / total_pages * 100
        else:
            return None
        return max(0, min(100, round(percent)))

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
                "type": self.progress_type or "",
                "value": self.progress_value,
            }
        return d

    @override
    @classmethod
    def params_from_ap_object(cls, post, obj, piece):
        content: str = obj.get("content", "").strip()
        attachments: list[dict[str, object]] = []
        params: dict[str, object] = {
            "title": obj.get("title", post.summary),
            "content": content,
            "sensitive": obj.get("sensitive", post.sensitive),
            "attachments": attachments,
        }
        if post.local:
            # for local post, strip footer and detect progress from content
            # if not detected, keep default/original value by not including it in return val
            params["content"], progress_type, progress_value = cls.strip_footer(content)
            if progress_value is not None:
                params["progress_type"] = progress_type
                params["progress_value"] = progress_value
        else:
            # for remote post, progress is always in "progress" field
            progress = obj.get("progress", {})
            params["progress_value"] = progress.get("value", None)
            params["progress_type"] = None
            if params["progress_value"]:
                t = progress.get("type", None)
                try:
                    params["progress_type"] = Note.ProgressType(t)
                except ValueError:
                    pass
        if post:
            for atta in post.attachments.all():
                attachments.append(
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
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        crosspost = (
            owner.local
            and owner.user.preference.mastodon_default_repost
            and owner.user.mastodon is not None
        )
        return super().update_by_ap_object(owner, item, obj, post, crosspost)

    @cached_property
    def shelfmember(self) -> ShelfMember | None:
        return ShelfMember.objects.filter(item=self.item, owner=self.owner).first()

    def to_crosspost_params(self):
        footer = f"\n—\n《{self.item.display_title}》 {self.progress_display}\n{self.item.absolute_url}"
        params = {
            "spoiler_text": self.title,
            "content": self.content + footer,
            "sensitive": self.sensitive,
            "reply_to_ids": (
                self.shelfmember.metadata.copy() if self.shelfmember else {}
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
    def strip_footer(cls, content: str) -> tuple[str, str | None, str | None]:
        """strip footer if 2nd last line is "-" or similar characters"""
        lines = content.splitlines()
        if len(lines) < 3 or lines[-2].strip() not in _separaters:
            return content, None, None
        progress_type, progress_value = cls.extract_progress(lines[-1])
        # if progress_value is None and not lines[-1].startswith("https://"):
        #     return content, None, None
        return (  # remove one extra empty line generated from <p> tags
            "\n".join(lines[: (-3 if lines[-3] == "" else -2)]),
            progress_type,
            progress_value,
        )

    @classmethod
    def extract_progress(cls, content) -> tuple[str | None, str | None]:
        m = _progress.match(content)
        if not m:
            m = _progress2.match(content)
        if m and m["value"]:
            if m["value"] == "-":
                return None, ""
            m = m.groupdict()
            typ_ = "percentage" if m["postfix"] == "%" else m.get("prefix", "")
            match typ_.lower():
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
    def get_progress_types_by_item(cls, item: Item) -> list[ProgressType]:
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

    def to_indexable_doc(self) -> dict[str, Any]:
        return {
            "item_id": [self.item.id],
            "item_class": [self.item.__class__.__name__],
            "item_title": self.item.to_indexable_titles(),
            "content": [self.title or "", self.content],
        }
