import dataclasses
import datetime
import json
import logging
import mimetypes
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from django.db import transaction
from django.utils.dateparse import parse_datetime

from common.models.lang import normalize_language
from journal.search.index import JournalIndex
from takahe.html import FediverseHtmlParser
from takahe.models import Hashtag, Post
from takahe.utils import Takahe

from .base import BaseImporter

logger = logging.getLogger(__name__)

# A Mastodon archive is a zip holding outbox.json (an ActivityPub
# OrderedCollection of the account's own Create and Announce activities),
# actor.json, likes.json, bookmarks.json and a media_attachments/ tree.
_OUTBOX = "outbox.json"
_ACTOR = "actor.json"
_MEDIA_DIR = "media_attachments"

# Uncompressed caps for members read out of a user-supplied zip.
_MAX_JSON_SIZE = 512 * 1024 * 1024
_MAX_MEDIA_SIZE = 5 * 1024 * 1024  # Takahe.upload_image refuses larger files
_SNIFF_SIZE = 1024 * 1024  # read to recognise a bare outbox.json

# Mastodon writes the public collection in full, but accepts the compact
# forms on the way in, so an archive of relayed posts can carry either.
_PUBLIC = {
    "https://www.w3.org/ns/activitystreams#Public",
    "as:Public",
    "Public",
}

_SPACES = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")
_INDEX_BATCH = 200


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _parse_time(value: Any) -> datetime.datetime | None:
    """``published`` is ISO 8601 (``2018-10-10T20:19:24Z``), to the second."""
    if not isinstance(value, str):
        return None
    try:
        dt = parse_datetime(value)
    except ValueError:
        return None
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.replace(microsecond=0)


def _fingerprint(text: str) -> str:
    """Enough of a body to tell two same-second posts apart."""
    return _SPACES.sub(" ", text).strip()[:40]


def _neutralise_handles(text: str) -> str:
    """Post bodies are plain text, so a handle left in one is resolved as a
    fediverse mention, which fetches the remote actor. The fullwidth at sign
    keeps the handle readable while the mention parser ignores it. The regex
    is Takahe's own, so exactly what it would resolve is defused."""
    return FediverseHtmlParser.MENTION_REGEX.sub(
        lambda m: f"{m.group(1)}＠{m.group(2)}", text
    )


def _mention_handles(note: dict) -> dict[str, str]:
    """Map the visible text of a mention link to a full handle.

    A mention link holds only ``@username`` (``TextFormatter#link_to_mention``)
    while the matching ``tag`` entry has ``name`` ``@username`` for an account
    local to the exporting server and ``@username@domain`` for any other. The
    bare form is qualified with the server the archive came from, so it cannot
    be read as a local user of this site."""
    handles: dict[str, str] = {}
    for tag in _tags(note, "Mention"):
        name = tag.get("name") or ""
        if not name.startswith("@"):
            continue
        handle = name[1:]
        if "@" not in handle:
            domain = urlparse(tag.get("href") or "").hostname
            if not domain:
                continue
            handle = f"{handle}@{domain}"
        handles[handle.split("@")[0].lower()] = handle
    return handles


def _tags(note: dict, type_: str) -> list[dict]:
    tags = note.get("tag")
    if isinstance(tags, dict):
        tags = [tags]
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, dict) and t.get("type") == type_]


def _as_dicts(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _order(uri: Any) -> int:
    """Mastodon status ids are chronological, so the number in the status uri
    orders the posts of one second. An activity uri ends in ``/activity``, so
    the segments are read from the end."""
    if not isinstance(uri, str):
        return 0
    for segment in reversed(urlparse(uri).path.split("/")):
        if segment.isdigit():
            return int(segment)
    return 0


@dataclasses.dataclass
class _Activity:
    """One outbox entry, ready to become a post."""

    published: datetime.datetime
    order: int
    content: str
    visibility: Takahe.Visibilities
    summary: str = ""
    sensitive: bool = False
    language: str = ""
    uris: list[str] = dataclasses.field(default_factory=list)
    reply_to: str = ""
    note: dict | None = None
    media: bool = False


class _ContentParser(HTMLParser):
    """Turn the HTML of a status into the plain text a local post carries.

    Takahe escapes whatever it is given and builds its own HTML, so the markup
    of the archive cannot be passed through. Links become their target, and
    hashtags and mentions become their text, which Takahe indexes again.
    """

    def __init__(self, mentions: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.mentions = mentions
        self.text = ""
        self._link: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        match tag:
            case "br":
                self.text += "\n"
            case "p" | "blockquote" | "pre" | "div":
                if self.text:
                    self.text += "\n\n"
            case "li":
                self.text += "\n"
            case "a":
                self._link = {"attrs": dict(attrs), "text": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._link is None:
            return
        attrs = self._link["attrs"]
        text = self._link["text"].strip()
        classes = (attrs.get("class") or "").split()
        self._link = None
        if attrs.get("rel") == "tag" or "hashtag" in classes:
            self.text += text  # "#tag", which Takahe picks up again
        elif "mention" in classes or text.startswith("@"):
            handle = self.mentions.get(text.lstrip("@").split("@")[0].lower())
            self.text += f"@{handle}" if handle else text
        else:
            self.text += attrs.get("href") or text

    def handle_data(self, data: str) -> None:
        if self._link is not None:
            self._link["text"] += data
        else:
            self.text += data


def _to_text(content: str, note: dict) -> str:
    parser = _ContentParser(_mention_handles(note))
    parser.feed(content.replace("\n", ""))
    parser.close()
    return _neutralise_handles(_BLANK_LINES.sub("\n\n", parser.text).strip())


class MastodonImporter(BaseImporter):
    """Import the posts of a Mastodon archive as local posts.

    Accepts the archive zip or a bare ``outbox.json``. Each post keeps the
    visibility recorded in the archive, so a direct message stays a direct
    message. Posts are created in the ``fanned_out`` state inside one
    transaction, so the state machine never sees them as new and nothing is
    delivered to followers or peers.
    """

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    _zip: zipfile.ZipFile | None = None
    _followers: str = ""

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        if not uploaded_file:
            return False
        try:
            if zipfile.is_zipfile(uploaded_file):
                uploaded_file.seek(0)
                with zipfile.ZipFile(uploaded_file) as zf:
                    names = zf.namelist()
                    return any(posixpath.basename(n) == _OUTBOX for n in names)
            uploaded_file.seek(0)
            # the full @context Mastodon writes ahead of the items is several
            # kilobytes, so the window has to be generous
            head = uploaded_file.read(_SNIFF_SIZE).lstrip()
            return head.startswith(b"{") and (
                b"orderedItems" in head or b"OrderedCollection" in head
            )
        except Exception:
            return False
        finally:
            try:
                uploaded_file.seek(0)
            except Exception:
                pass

    # ---- archive access -------------------------------------------------

    def _load(self, path: str) -> tuple[list[dict], dict[str, str]]:
        """Return (activities, media members) from a zip or a bare json file.
        Media is keyed both by its path inside the archive and by file name,
        because the path an attachment points at depends on how the exporting
        server stored the file."""
        if not zipfile.is_zipfile(path):
            self._zip = None
            with open(path, "rb") as f:
                return self._items(json.load(f)), {}
        self._zip = zipfile.ZipFile(path)
        items: list[dict] = []
        media: dict[str, str] = {}
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            name = posixpath.basename(info.filename)
            if name in (_OUTBOX, _ACTOR):
                if info.file_size > _MAX_JSON_SIZE:
                    raise ValueError(f"{info.filename} is too large")
                with self._zip.open(info) as f:
                    data = json.load(f)
                if name == _OUTBOX:
                    items = self._items(data)
                elif isinstance(data, dict):
                    self._followers = data.get("followers") or ""
            elif (
                _MEDIA_DIR in info.filename.split("/")
                and info.file_size <= _MAX_MEDIA_SIZE
            ):
                media[info.filename] = info.filename
                media.setdefault(name, info.filename)
        if not items:
            raise ValueError("no outbox found in archive")
        return items, media

    @staticmethod
    def _items(data: Any) -> list[dict]:
        if not isinstance(data, dict):
            raise ValueError("unexpected archive layout")
        items = data.get("orderedItems") or data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("unexpected archive layout")
        return [i for i in items if isinstance(i, dict)]

    def _read_media(self, member: str) -> bytes:
        assert self._zip is not None
        with self._zip.open(member) as f:
            return f.read()

    # ---- activity to post -----------------------------------------------

    def _visibility(self, item: dict, note: dict | None) -> Takahe.Visibilities:
        """Read the visibility off the audience the way Mastodon itself does
        (``ActivityPub::Parser::StatusParser#visibility``). The archive is the
        only source: the posting default of the account is not applied, so a
        post keeps the reach it had, and a direct message stays direct."""
        source = note if note is not None else item
        to = _as_list(source.get("to")) or _as_list(item.get("to"))
        cc = _as_list(source.get("cc")) or _as_list(item.get("cc"))
        if any(a in _PUBLIC for a in to):
            return Takahe.Visibilities.public
        if any(a in _PUBLIC for a in cc):
            return Takahe.Visibilities.unlisted
        # a bare outbox has no actor.json to name the followers collection
        if (self._followers and self._followers in to) or (
            not self._followers and any(a.endswith("/followers") for a in to)
        ):
            return Takahe.Visibilities.followers
        return Takahe.Visibilities.mentioned

    @staticmethod
    def _content(note: dict) -> str:
        """The post body: the status text with its markup flattened, and the
        options of a poll listed under it."""
        text = _to_text(note.get("content") or "", note)
        options = note.get("oneOf") or note.get("anyOf") or []
        names = [
            o["name"]
            for o in options
            if isinstance(o, dict) and isinstance(o.get("name"), str) and o["name"]
        ]
        if names:
            text = "\n\n".join(filter(None, [text, "\n".join(f"- {n}" for n in names)]))
        return text

    @staticmethod
    def _language(note: dict) -> str:
        """``contentMap`` is keyed by the language of the status, which can
        name a region (``zh-CN``). Posts of this site carry a macrolanguage,
        the way ``User.macrolanguage`` gives it."""
        content_map = note.get("contentMap")
        lang = ""
        if isinstance(content_map, dict):
            for key in content_map:
                if isinstance(key, str) and key:
                    lang = normalize_language(key) or ""
                    break
        lang = lang.split("-")[0]
        return "" if lang == "und" else lang

    def _attachments(self, note: dict, media: dict[str, str]):
        attachments = []
        for a in _as_dicts(note.get("attachment")):
            # a preview card is a Link with no url, and only images can be
            # attached to a local post
            if a.get("type") != "Document":
                continue
            url = a.get("url") or ""
            if not url:
                continue
            filename = posixpath.basename(urlparse(url).path)
            member = media.get(url.lstrip("/")) or media.get(filename)
            if not member:
                continue
            mimetype = a.get("mediaType") or mimetypes.guess_type(filename)[0]
            if not mimetype or not mimetype.startswith("image/"):
                continue
            try:
                attachments.append(
                    Takahe.upload_image(
                        self.user.identity.pk,
                        filename,
                        self._read_media(member),
                        mimetype,
                        description=a.get("name") or "",
                    )
                )
            except Exception as e:
                logger.warning(f"skipping media {filename}: {e}")
        return attachments

    def _existing_post(self, published: datetime.datetime, content: str) -> Post | None:
        """An imported post carries no source id, so it is recognised by its
        author, its publish second (which the archive records exactly) and the
        start of its text: a thread posted at once can share one second."""
        wanted = _fingerprint(content)
        second = published.replace(microsecond=0)
        posts = Post.objects.filter(
            author_id=self.user.identity.pk,
            local=True,
            published__gte=second,
            published__lt=second + datetime.timedelta(seconds=1),
        )
        for post in posts:
            if _fingerprint(post.content_plain_text) == wanted:
                return post
        return None

    def import_activity(
        self,
        activity: "_Activity",
        reply_to: Post | None,
        media: dict[str, str],
    ) -> tuple[BaseImporter.ImportResult, Post | None]:
        try:
            if not activity.content and not activity.media:
                return "skipped", None
            if self._existing_post(activity.published, activity.content):
                return "skipped", None
            attachments = (
                self._attachments(activity.note, media) if activity.note else []
            )
            with transaction.atomic(using="takahe"):
                post = Takahe.post(
                    self.user.identity.pk,
                    activity.content,
                    activity.visibility,
                    summary=activity.summary or None,
                    sensitive=activity.sensitive,
                    post_time=activity.published,
                    reply_to_pk=reply_to.pk if reply_to else None,
                    attachments=attachments or None,
                    language=activity.language,
                )
                if not post:
                    return "failed", None
                # never let the state machine see a "new" post: no FanOut rows
                # are created, so no follower or peer is ever notified
                if post.state != "fanned_out":
                    Post.objects.filter(pk=post.pk).update(state="fanned_out")
                    post.state = "fanned_out"
            # fan-out is what normally registers hashtags
            for tag in post.hashtags or []:
                Hashtag.ensure_hashtag(tag, update=True)
            return "imported", post
        except Exception:
            source = activity.uris[0] if activity.uris else activity.published
            logger.exception(f"Error importing {source}")
            return "failed", None

    # ---- run --------------------------------------------------------------

    def run(self) -> None:
        items, media = self._load(self.metadata["file"])
        activities = self._plan(items)

        self.metadata["total"] = len(activities)
        self.message = f"found {len(activities)} posts to import"
        self.save(update_fields=["metadata", "message"])

        by_uri = {uri: a for a in activities for uri in a.uris}
        posts: dict[str, Post] = {}
        pending: list[Post] = []
        for activity in activities:
            reply_to = None
            if activity.reply_to:
                parent = by_uri.get(activity.reply_to)
                reply_to = posts.get(activity.reply_to) or (
                    # a parent imported by an earlier run is matched the way a
                    # duplicate is
                    self._existing_post(parent.published, parent.content)
                    if parent
                    else None
                )
            result, post = self.import_activity(activity, reply_to, media)
            self.progress(result)
            if post:
                for uri in activity.uris:
                    posts[uri] = post
                pending.append(post)
                if len(pending) >= _INDEX_BATCH:
                    self._index(pending)
                    pending = []
        self._index(pending)
        if self._zip:
            self._zip.close()

        self.message = (
            f"{self.metadata['imported']} imported, "
            f"{self.metadata['skipped']} skipped, "
            f"{self.metadata['failed']} failed."
        )
        self.save(update_fields=["message"])

    def _plan(self, items: list[dict]) -> list["_Activity"]:
        """Read every activity of the outbox, oldest first.

        Mastodon records seconds only, so posts of one thread can share a
        second. They get millisecond offsets in id order, which keeps the
        thread in order in post ids and in timelines. The extra half
        millisecond survives the float truncation in the id generator.
        """
        activities: list[_Activity] = []
        for item in items:
            activity = self._activity(item)
            if activity:
                activities.append(activity)
        activities.sort(key=lambda a: (a.published, a.order))
        last: datetime.datetime | None = None
        offset = 0
        for activity in activities:
            if activity.published == last:
                offset += 1
                activity.published += datetime.timedelta(
                    milliseconds=offset, microseconds=500
                )
            else:
                last, offset = activity.published, 0
        return activities

    def _activity(self, item: dict) -> "_Activity | None":
        type_ = item.get("type")
        obj = item.get("object")
        published = _parse_time(item.get("published"))
        if type_ == "Announce":
            # a boost of the account's own followers-only post is inlined, and
            # is in the outbox as a Create of its own
            if not isinstance(obj, str) or not published:
                return None
            return _Activity(
                published=published,
                order=_order(item.get("id")),
                content=f"🔁 {obj}",
                visibility=self._visibility(item, None),
            )
        if type_ != "Create" or not isinstance(obj, dict):
            return None
        published = _parse_time(obj.get("published")) or published
        if not published:
            logger.warning(f"{obj.get('id')} has no valid date")
            return None
        content = self._content(obj)
        media = bool(_as_dicts(obj.get("attachment")))
        if not content and not media:
            return None
        uris = [
            u
            for u in (obj.get("id"), obj.get("url"), obj.get("atomUri"))
            if isinstance(u, str) and u
        ]
        reply_to = obj.get("inReplyTo") or obj.get("inReplyToAtomUri")
        return _Activity(
            published=published,
            order=_order(obj.get("id")),
            content=content,
            visibility=self._visibility(item, obj),
            summary=obj.get("summary") or "",
            sensitive=bool(obj.get("sensitive")),
            language=self._language(obj),
            uris=uris,
            reply_to=reply_to if isinstance(reply_to, str) else "",
            note=obj,
            media=media,
        )

    @staticmethod
    def _index(posts: list[Post]) -> None:
        if not posts:
            return
        try:
            JournalIndex.instance().replace_posts(posts)
        except Exception as e:
            logger.error(f"indexing imported posts failed: {e}")
