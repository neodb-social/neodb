import re
from datetime import datetime
from typing import Any

from django.db import models
from django.utils import timezone
from loguru import logger
from markdownify import markdownify as md

from takahe.utils import Takahe
from users.models import APIdentity

from .common import Piece, VisibilityType
from .renderers import render_md
from .tag import Tag as TagModel

_RE_HTML_TAG = re.compile(r"<[^>]*>")
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
    edited_time = models.DateTimeField(auto_now=True)
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
        html = self.html_content
        return _RE_HTML_TAG.sub(
            " ", _RE_SPOILER_TAG.sub("***", html.replace("\n", " "))
        )

    @property
    def brief_description(self) -> str:
        return self.plain_content[:155]

    @property
    def normalized_tags(self) -> list[str]:
        return _normalize_tags(self.tags)

    @property
    def ap_object(self) -> dict:
        d = {
            "id": self.absolute_url,
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
        if self.summary:
            d["summary"] = self.summary
        return d

    def get_ap_data(self) -> dict:
        # Standalone Article has no related Item or Piece; omit `relatedWith`
        # so it is not mistaken for a NeoDB-managed Review on receivers.
        return {"object": self.ap_object}

    def to_post_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "post_type": "Article",
            "content": self.html_content,
            "summary": self.summary or None,
            "sensitive": self.sensitive,
            "language": self.language or "",
        }
        # `_pending_attachments` is set by the view after Takahe.upload_image
        # so freshly uploaded PostAttachments get linked to the post on
        # create/edit. When absent, we omit the key so existing post
        # attachments are preserved (mirrors Note.to_post_params).
        pending = getattr(self, "_pending_attachments", None)
        if pending:
            params["attachments"] = list(pending)
        return params

    def to_crosspost_params(self) -> dict[str, Any]:
        body = self.plain_content[:300]
        if len(self.plain_content) > 300:
            body += "..."
        content = f"{self.title}\n\n{body}\n\n{self.absolute_url}"
        return {"content": content, "spoiler_text": self.summary or None}

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
        try:
            edited = datetime.fromisoformat(updated_iso)
            published = datetime.fromisoformat(published_iso)
        except ValueError:
            logger.warning(f"Article {obj.get('id')} has unparseable timestamps")
            return None
        d = cls.params_from_ap_object(post, obj, existing)
        if existing:
            if existing.edited_time >= edited:
                return existing  # stale; no-op
            for k, v in d.items():
                setattr(existing, k, v)
            existing.edited_time = edited
            existing.save(
                update_fields=list(d.keys()) + ["edited_time"],
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
        attachments: list[dict] | None = None,
        post_attachments: list | None = None,
        article: "Article | None" = None,
        share_to_mastodon: bool = False,
        application_id: int | None = None,
    ) -> "Article":
        if article is None:
            article = cls(owner=owner)
        article.title = title
        article.body = body
        article.summary = summary or ""
        article.sensitive = bool(sensitive)
        article.visibility = int(visibility)
        article.language = language or ""
        article.tags = _normalize_tags(tags or [])
        if attachments is not None:
            article.attachments = attachments
        if post_attachments:
            setattr(article, "_pending_attachments", post_attachments)
        article.crosspost_when_save = share_to_mastodon
        article.application_id_when_save = application_id
        article.save()
        return article
