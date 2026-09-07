import datetime
import html
import json
import logging
import mimetypes
import posixpath
import re
import zipfile
from typing import Any

from django.db import transaction
from django.utils.dateparse import parse_datetime

from journal.search.index import JournalIndex
from takahe.models import Hashtag, Post
from takahe.utils import Takahe

from .base import BaseImporter

logger = logging.getLogger(__name__)

# A Twitter archive is a zip whose data/ folder holds one JS file per data
# type. Each file is a JSON array assigned to a window.YTD.* global.
_TWEET_FILES = re.compile(r"^(tweets?|tweets-part\d+)\.js$")
_NOTE_FILES = re.compile(r"^note-tweet(-part\d+)?\.js$")
_MEDIA_DIRS = ("tweets_media", "tweet_media")
_JS_PREFIX = re.compile(rb"^\s*window\.YTD\.(note_)?tweets?\.part\d+\s*=")

# Uncompressed caps for members read out of a user-supplied zip.
_MAX_JS_SIZE = 512 * 1024 * 1024
_MAX_MEDIA_SIZE = 5 * 1024 * 1024  # Takahe.upload_image refuses larger files

_HANDLE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{1,15})\b")
_SPACES = re.compile(r"\s+")
_INDEX_BATCH = 200


def _parse_js(raw: bytes) -> list[Any]:
    """Turn ``window.YTD.tweets.part0 = [...]`` into the JSON array."""
    if not _JS_PREFIX.match(raw):
        raise ValueError("not a Twitter archive data file")
    data = json.loads(raw.split(b"=", 1)[1])
    if not isinstance(data, list):
        raise ValueError("unexpected archive layout")
    return data


def _parse_tweet_time(value: str) -> datetime.datetime | None:
    """``created_at`` looks like ``Wed Oct 10 20:19:24 +0000 2018``."""
    try:
        return datetime.datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except TypeError, ValueError:
        return None


def _parse_note_time(value: str) -> datetime.datetime | None:
    """Note tweets use ISO 8601 (``2023-02-08T15:12:34.000Z``). They are
    matched to their tweet on the second, so the fraction is dropped."""
    try:
        dt = parse_datetime(value) if value else None
    except ValueError:
        return None
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.replace(microsecond=0)


def _note_matches(tweet: dict, note: dict) -> bool:
    """A note tweet carries no tweet id, only a timestamp. Two tweets can
    share a second, so the truncated tweet text (minus the trailing ellipsis
    and the self link) must also be a prefix of the note text."""
    text = html.unescape(tweet.get("full_text") or tweet.get("text") or "")
    for u in tweet.get("entities", {}).get("urls", []):
        if u.get("url"):
            text = text.replace(u["url"], "")
    text = text.strip().rstrip("…").rstrip()
    full = html.unescape(note.get("core", {}).get("text", ""))
    return bool(text) and full.startswith(text[:50])


def _fingerprint(text: str) -> str:
    """Enough of a body to tell two same-second posts apart."""
    return _SPACES.sub(" ", text).strip()[:40]


def _neutralise_handles(text: str) -> str:
    """Post bodies are plain text, so a Twitter ``@handle`` would be resolved
    as a fediverse mention of whatever local user shares the name. The
    fullwidth at sign keeps it readable while the mention parser ignores it."""
    return _HANDLE.sub(lambda m: f"＠{m.group(1)}", text)


class TwitterImporter(BaseImporter):
    """Import the tweets of a Twitter/X archive as local posts.

    Accepts the archive zip or a bare ``tweets.js``. Posts are created in the
    ``fanned_out`` state inside one transaction, so the state machine never
    sees them as new and nothing is delivered to followers or peers.
    """

    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    _zip: zipfile.ZipFile | None = None
    _post_visibility: Takahe.Visibilities = Takahe.Visibilities.public

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        if not uploaded_file:
            return False
        try:
            if zipfile.is_zipfile(uploaded_file):
                uploaded_file.seek(0)
                with zipfile.ZipFile(uploaded_file) as zf:
                    return any(
                        _TWEET_FILES.match(posixpath.basename(n)) for n in zf.namelist()
                    )
            uploaded_file.seek(0)
            return bool(_JS_PREFIX.match(uploaded_file.read(64)))
        except Exception:
            return False
        finally:
            try:
                uploaded_file.seek(0)
            except Exception:
                pass

    # ---- archive access -------------------------------------------------

    def _load(self, path: str) -> tuple[list[dict], list[dict], dict[str, str]]:
        """Return (tweets, notes, media members) from a zip or a bare js file.
        Media is keyed by file name and maps to the zip member path."""
        if zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
            tweets: list[dict] = []
            notes: list[dict] = []
            media: dict[str, str] = {}
            for info in self._zip.infolist():
                if info.is_dir():
                    continue
                name = posixpath.basename(info.filename)
                parent = posixpath.basename(posixpath.dirname(info.filename))
                if _TWEET_FILES.match(name) or _NOTE_FILES.match(name):
                    if info.file_size > _MAX_JS_SIZE:
                        raise ValueError(f"{info.filename} is too large")
                    with self._zip.open(info) as f:
                        entries = _parse_js(f.read())
                    if _NOTE_FILES.match(name):
                        notes += entries
                    else:
                        tweets += entries
                elif parent in _MEDIA_DIRS and info.file_size <= _MAX_MEDIA_SIZE:
                    media[name] = info.filename
            return tweets, notes, media
        self._zip = None
        with open(path, "rb") as f:
            return _parse_js(f.read()), [], {}

    def _read_media(self, member: str) -> bytes:
        assert self._zip is not None
        with self._zip.open(member) as f:
            return f.read()

    # ---- tweet to post --------------------------------------------------

    @staticmethod
    def _content(tweet: dict, note: dict | None) -> str:
        """Build the post body: full text (from the note tweet when the tweet
        was truncated), t.co links expanded, media links dropped, handles
        neutralised. The archive stores text HTML-escaped; the post body is
        plain text, so it is unescaped here."""
        if note:
            core = note.get("core", {})
            text = html.unescape(core.get("text", ""))
            urls = [
                (u.get("url", ""), u.get("expandedUrl", ""))
                for u in core.get("urls", [])
            ]
        else:
            text = html.unescape(tweet.get("full_text") or tweet.get("text") or "")
            urls = [
                (u.get("url", ""), u.get("expanded_url", ""))
                for u in tweet.get("entities", {}).get("urls", [])
            ]
        for short, expanded in urls:
            if short and expanded:
                text = text.replace(short, expanded)
        for m in TwitterImporter._media(tweet):
            if m.get("url"):
                text = text.replace(m["url"], "")
        return _neutralise_handles(text.strip())

    @staticmethod
    def _media(tweet: dict) -> list[dict]:
        ext = tweet.get("extended_entities") or {}
        return ext.get("media") or tweet.get("entities", {}).get("media") or []

    def _attachments(self, tweet: dict, tweet_id: str, media: dict[str, str]):
        attachments = []
        for m in self._media(tweet):
            if m.get("type") != "photo":
                continue  # videos and animated gifs are not imported
            url = m.get("media_url_https") or m.get("media_url") or ""
            filename = f"{tweet_id}-{posixpath.basename(url)}"
            member = media.get(filename)
            if not member:
                continue
            mimetype = mimetypes.guess_type(filename)[0]
            if not mimetype or not mimetype.startswith("image/"):
                continue
            try:
                attachments.append(
                    Takahe.upload_image(
                        self.user.identity.pk,
                        filename,
                        self._read_media(member),
                        mimetype,
                        description=m.get("ext_alt_text") or "",
                    )
                )
            except Exception as e:
                logger.warning(f"skipping media {filename}: {e}")
        return attachments

    def _existing_post(self, published: datetime.datetime, content: str) -> Post | None:
        """Posts carry no source id, so an imported tweet is recognised by its
        author, its publish second (which Twitter records exactly) and the
        start of its text: a thread posted at once shares one second."""
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

    def import_tweet(
        self,
        tweet: dict,
        note: dict | None,
        published: datetime.datetime | None,
        parent: tuple[datetime.datetime, str] | None,
        media: dict[str, str],
    ) -> tuple[BaseImporter.ImportResult, Post | None]:
        """``parent`` is the publish time and body of the tweet this one
        replies to, when that tweet is the user's own."""
        try:
            text = tweet.get("full_text") or tweet.get("text") or ""
            if text.startswith("RT @") or "retweeted_status" in tweet:
                return "skipped", None
            if not published:
                logger.warning(f"tweet {tweet.get('id_str')} has no valid date")
                return "failed", None
            content = self._content(tweet, note)
            if not content and not self._media(tweet):
                return "skipped", None
            if self._existing_post(published, content):
                return "skipped", None
            tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
            reply_to = self._existing_post(*parent) if parent else None
            lang = tweet.get("lang") or ""
            with transaction.atomic(using="takahe"):
                post = Takahe.post(
                    self.user.identity.pk,
                    content,
                    self._post_visibility,
                    sensitive=bool(tweet.get("possibly_sensitive")),
                    post_time=published,
                    reply_to_pk=reply_to.pk if reply_to else None,
                    attachments=self._attachments(tweet, tweet_id, media) or None,
                    language="" if lang == "und" else lang,
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
            logger.exception("Error importing tweet")
            return "failed", None

    # ---- run --------------------------------------------------------------

    def run(self) -> None:
        self._post_visibility = Takahe.visibility_n2t(
            self.metadata.get("visibility", 0),
            self.user.preference.post_public_mode,
        )
        tweets, notes, media = self._load(self.metadata["file"])
        tweets = [t.get("tweet", t) for t in tweets]
        tweets.sort(key=lambda t: int(t.get("id_str") or t.get("id") or 0))
        by_id = {str(t.get("id_str") or t.get("id")): t for t in tweets}
        # Twitter records seconds only. A thread posted at once shares one
        # second, so tweets in the same second get millisecond offsets in id
        # order: post ids and timelines then keep the thread order. The extra
        # half millisecond survives the float truncation in the id generator.
        published_of: dict[str, datetime.datetime | None] = {}
        last: datetime.datetime | None = None
        offset = 0
        for tweet_id, t in by_id.items():
            created = _parse_tweet_time(t.get("created_at", ""))
            if created and created == last:
                offset += 1
                created = created + datetime.timedelta(
                    milliseconds=offset, microseconds=500
                )
            else:
                last, offset = created, 0
            published_of[tweet_id] = created
        # note tweets carry no tweet id; they are created in the same second
        # as the truncated tweet they belong to
        notes_by_time: dict[datetime.datetime, dict] = {}
        for n in notes:
            n = n.get("noteTweet", n)
            created = _parse_note_time(n.get("createdAt", ""))
            if created:
                notes_by_time[created] = n

        def note_for(tweet: dict) -> dict | None:
            created = _parse_tweet_time(tweet.get("created_at", ""))
            note = notes_by_time.get(created) if created else None
            return note if note and _note_matches(tweet, note) else None

        def parent_of(tweet: dict) -> tuple[datetime.datetime, str] | None:
            parent_id = tweet.get("in_reply_to_status_id_str") or tweet.get(
                "in_reply_to_status_id"
            )
            parent = by_id.get(str(parent_id)) if parent_id else None
            published = published_of.get(str(parent_id)) if parent else None
            if not parent or not published:
                return None
            return published, self._content(parent, note_for(parent))

        self.metadata["total"] = len(tweets)
        self.message = f"found {len(tweets)} tweets to import"
        self.save(update_fields=["metadata", "message"])

        pending: list[Post] = []
        for tweet_id, tweet in by_id.items():
            result, post = self.import_tweet(
                tweet, note_for(tweet), published_of[tweet_id], parent_of(tweet), media
            )
            self.progress(result)
            if post:
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

    @staticmethod
    def _index(posts: list[Post]) -> None:
        if not posts:
            return
        try:
            JournalIndex.instance().replace_posts(posts)
        except Exception as e:
            logger.error(f"indexing imported posts failed: {e}")
