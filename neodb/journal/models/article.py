import re
from typing import Any

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from loguru import logger
from markdownify import markdownify as md

from takahe.utils import Takahe
from users.models import APIdentity

from .atproto import DOCUMENT_NSID, build_document
from .common import Piece, VisibilityType
from .renderers import render_md, sanitize_md_images
from .tag import Tag as TagModel

_RE_SPOILER_TAG = re.compile(r'<(div|span)\sclass="spoiler">.*</(div|span)>')

_TAG_MAX_COUNT = 30


def _normalize_tags(values: Any) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        title = TagModel.cleanup_title(raw, replace=False)
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(title)
        if len(out) >= _TAG_MAX_COUNT:
            break
    return out


class Article(Piece):
    """A standalone, item-less, markdown-authored ActivityPub Article.

    Reuses Piece machinery for federation/index/post linkage. Field set
    mirrors Content (which requires an Item FK) without inheriting from it.
    """

    url_path = "article"
    post_when_save = True
    index_when_save = True

    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        choices=VisibilityType.choices, default=0, null=False
    )
    created_time = models.DateTimeField(default=timezone.now)
    # NB: NOT ``auto_now=True``. Inbound federation copies the upstream
    # ``updated`` timestamp here and the staleness guard in
    # ``update_by_ap_object`` compares against it; ``auto_now`` would
    # silently overwrite it on save and turn legitimate later Updates
    # into "stale, ignored". ``update_local_article`` sets this
    # explicitly for local edits.
    edited_time = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict)
    remote_id = models.CharField(max_length=200, null=True, default=None)

    title = models.CharField(max_length=500, blank=False, null=False)
    body = models.TextField()
    summary = models.TextField(blank=True, default="")
    sensitive = models.BooleanField(default=False)
    language = models.CharField(max_length=8, blank=True, default="")
    attachments = models.JSONField(default=list)
    tags = models.JSONField(default=list)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "created_time"]),
            models.Index(fields=["remote_id"], name="article_remote_id_idx"),
        ]

    def __str__(self):
        return f"Article:{self.uuid}"

    @property
    def display_title(self) -> str:
        return self.title

    @property
    def html_content(self) -> str:
        return render_md(self.body)

    @property
    def plain_content(self) -> str:
        html = self.html_content.replace("\n", " ")
        # Mask spoiler blocks first, then strip remaining HTML tags. Mirrors
        # ``Review.plain_content`` but uses Django's ``strip_tags`` instead
        # of a hand-rolled regex (more robust against pathological markup).
        return strip_tags(_RE_SPOILER_TAG.sub("***", html))

    @property
    def brief_description(self) -> str:
        """Short teaser used in link-preview cards (e.g. the Bluesky embed):
        the author summary (with sensitive marker, so a sensitive article
        previews as its marker rather than its body) when present, else the
        body's leading plain text."""
        return (self.display_summary or self.plain_content)[:155]

    @property
    def excerpt(self) -> str:
        """Short snippet for the timeline teaser (first paragraph-ish)."""
        text = self.plain_content.strip()
        if len(text) <= 220:
            return text
        return text[:220].rstrip() + "…"

    @property
    def word_count(self) -> int:
        """Whitespace-token count of the rendered plain text. Persisted in
        ``metadata['word_count']`` at save time; falls back to live compute
        for rows saved before this field existed (no migration required)."""
        cached = (self.metadata or {}).get("word_count")
        if isinstance(cached, int):
            return cached
        return len(self.plain_content.split())

    def _compute_word_count(self) -> int:
        return len(self.plain_content.split())

    @property
    def normalized_tags(self) -> list[str]:
        return _normalize_tags(self.tags)

    @property
    def edit_url(self) -> str:
        return reverse("journal:article_edit", args=[self.uuid])

    @property
    def delete_url(self) -> str:
        return reverse("journal:article_delete", args=[self.uuid])

    @property
    def display_summary(self) -> str:
        """Author-supplied ``summary`` plus auto sensitive marker. Used in
        timeline teasers and emitted as the AS wire ``summary`` field. The
        marker is the user-facing translation of "(may contain sensitive
        content)" appended in parens; it appears regardless of whether
        the user wrote a summary so receivers always see the cue."""
        text = (self.summary or "").strip()
        if self.sensitive:
            marker = _("(may contain sensitive content)")
            if text:
                text = text + " " + marker
            else:
                text = marker
        return text

    @property
    def ap_object(self) -> dict:
        # No ``id`` key on purpose: ``get_ap_data()`` is merged into the
        # wrapping Takahe ``Post.type_data["object"]``, and any ``id`` here
        # would clobber Takahe's canonical object URI. Remote likes/replies
        # are keyed off that URI via ``Post.by_object_uri``, so leaving it
        # alone is required for round-trip federation. ``href`` continues
        # to point at NeoDB's browsing URL for humans following the link.
        d = {
            "type": "Article",
            "name": self.title,
            "content": self.html_content,
            "source": {"content": self.body, "mediaType": "text/markdown"},
            "sensitive": self.sensitive,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "href": self.absolute_url,
            "tag": [{"type": "Hashtag", "name": f"#{t}"} for t in self.normalized_tags],
        }
        # AS ``summary`` here carries the *raw* user-supplied text only.
        # NDJSON export and inbound ``update_by_ap_object`` both round-trip
        # through ``ap_object``, so storing the auto-marker here would
        # double it on re-import. ``get_ap_data`` injects the decorated
        # ``display_summary`` on the federation path so peers still see
        # the sensitivity cue alongside the title + URL teaser that
        # Mastodon synthesises for converted types.
        if self.summary:
            d["summary"] = self.summary
        return d

    def get_ap_data(self) -> dict:
        # Standalone Article has no related Item or Piece; omit `relatedWith`
        # so it is not mistaken for a NeoDB-managed Review on receivers.
        obj = dict(self.ap_object)
        text = self.display_summary
        if text:
            obj["summary"] = text
        return {"object": obj}

    def to_post_params(self) -> dict[str, Any]:
        # Images embedded inline in the markdown body live in the rendered
        # HTML ``content`` (uploaded via the EasyMDE upload-image flow);
        # we don't surface them as separate AP ``attachment`` entries.
        return {
            "post_type": "Article",
            "content": self.html_content,
            "summary": self.summary or None,
            "sensitive": self.sensitive,
            "language": self.language or "",
        }

    def to_crosspost_params(self) -> dict[str, Any]:
        # ##obj## renders as a title link on Bluesky (which also gets an
        # external embed card for the article) and as plain title text on
        # Mastodon/Threads, where ##obj_link_if_plain## then carries the URL;
        # an inline URL would be lost to Bluesky's grapheme-limit truncation
        body = self.plain_content[:300]
        if len(self.plain_content) > 300:
            body += "..."
        content = f"##obj##\n\n{body}\n##obj_link_if_plain##"
        return {"content": content, "obj": self, "spoiler_text": self.summary or None}

    def atproto_document_collections(self) -> set[str]:
        return {DOCUMENT_NSID}

    def to_atproto_document(self) -> dict[str, Any]:
        # display_summary carries the sensitive-content marker when set, so
        # external previews keep the cue; fall back to the body excerpt
        return build_document(
            self,
            title=self.title,
            body=self.body,
            text=self.plain_content,
            description=self.display_summary or self.excerpt,
            tags=self.normalized_tags,
        )

    def to_indexable_doc(self) -> dict[str, Any]:
        return {
            "content": [self.title, self.body],
            "tag": [
                t
                for t in (TagModel.deep_cleanup_title(s) for s in self.normalized_tags)
                if t and t != "_"
            ],
        }

    @classmethod
    def params_from_ap_object(cls, post, obj, piece):
        source = obj.get("source") or {}
        if source.get("mediaType") == "text/markdown" and source.get("content"):
            body = source["content"]
        else:
            body = md(obj.get("content") or "")
        tags: list[str] = []
        for t in obj.get("tag", []) or []:
            if isinstance(t, dict) and t.get("type") == "Hashtag":
                name = (t.get("name") or "").lstrip("#")
                if name:
                    tags.append(name)
        return {
            "title": (obj.get("name") or "")[:500],
            "body": body,
            "summary": obj.get("summary") or "",
            "sensitive": bool(obj.get("sensitive", False)),
            "language": post.language or "",
            "tags": _normalize_tags(tags),
        }

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        """Persist a federated standalone Article into the local DB.

        ``item`` is unused (Articles are item-less) but kept for the shared
        ``Piece`` dispatcher signature. Returns the Article on success;
        ``None`` for local-author posts (their authoritative path is
        ``update_local_article`` via the form), owner mismatches, and
        envelopes that lack a ``published`` timestamp.
        """
        if post.local:
            return None
        published_iso = obj.get("published")
        if not published_iso:
            return None
        existing = cls.get_by_post_id(post.id)
        if existing and existing.owner_id != post.author_id:
            logger.warning(
                f"Article owner mismatch: {existing.owner_id} != {post.author_id}"
            )
            return None
        updated_iso = obj.get("updated") or published_iso
        # ``parse_datetime`` handles the ``Z`` UTC suffix on every Python
        # version we ship; ``datetime.fromisoformat`` only does so on 3.11+.
        edited = parse_datetime(updated_iso)
        published = parse_datetime(published_iso)
        if edited is None or published is None:
            logger.warning(f"Article {obj.get('id')} has unparseable timestamps")
            return None
        d = cls.params_from_ap_object(post, obj, existing)
        if existing:
            if existing.edited_time >= edited:
                return existing  # stale; no-op
            for k, v in d.items():
                setattr(existing, k, v)
            existing.edited_time = edited
            existing.metadata = {
                **(existing.metadata or {}),
                "word_count": existing._compute_word_count(),
            }
            existing.save(
                update_fields=list(d.keys()) + ["edited_time", "metadata"],
                post_when_save=False,
            )
            return existing
        visibility = Takahe.visibility_t2n(post.visibility)
        d.update(
            {
                "owner": owner,
                "local": False,
                "visibility": visibility,
                "remote_id": obj.get("id"),
                "created_time": published,
                "edited_time": edited,
            }
        )
        article = cls(**d)
        article.metadata = {"word_count": article._compute_word_count()}
        article.previous_visibility = visibility
        article.save(link_post_id=post.id, post_when_save=False)
        return article

    @classmethod
    def update_local_article(
        cls,
        owner: APIdentity,
        title: str,
        body: str,
        *,
        summary: str = "",
        sensitive: bool = False,
        visibility: int = 0,
        language: str = "",
        tags: list[str] | None = None,
        article: "Article | None" = None,
        share_to_mastodon: bool = False,
        application_id: int | None = None,
    ) -> "Article":
        if article is None:
            article = cls(owner=owner)
        article.title = title
        # Validate/normalize markdown image srcs here (not in each caller) so
        # every local-author entry point — web compose form and the REST API —
        # is sanitized by default. Idempotent: already-normalized srcs are
        # unchanged. Inbound federation uses update_by_ap_object, not this path.
        article.body = sanitize_md_images(body)
        article.summary = summary or ""
        article.sensitive = bool(sensitive)
        article.visibility = int(visibility)
        article.language = language or ""
        article.tags = _normalize_tags(tags or [])
        article.edited_time = timezone.now()
        article.metadata = {
            **(article.metadata or {}),
            "word_count": article._compute_word_count(),
        }
        article.crosspost_when_save = share_to_mastodon
        article.application_id_when_save = application_id
        article.save()
        return article
