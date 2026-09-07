import datetime
import json
import zipfile
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from PIL import Image

from common.models import SiteConfig
from journal.importers import MastodonImporter
from takahe.models import FanOut, Post
from users.models import User

_HOST = "https://mastodon.example"
_ACTOR = f"{_HOST}/users/masto"
_FOLLOWERS = f"{_ACTOR}/followers"
_PUBLIC = "https://www.w3.org/ns/activitystreams#Public"


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def _note(
    status_id: str = "1000",
    content: str = "<p>Hello world</p>",
    published: str = "2021-05-04T09:08:07Z",
    to: list | None = None,
    cc: list | None = None,
    reply_to: str | None = None,
    **extra,
) -> dict:
    """One Create activity, shaped like ActivityPub::CreateNoteSerializer."""
    uri = f"{_ACTOR}/statuses/{status_id}"
    parent = f"{_ACTOR}/statuses/{reply_to}" if reply_to else None
    audience_to = [_PUBLIC] if to is None else to
    audience_cc = [_FOLLOWERS] if cc is None else cc
    note = {
        "id": uri,
        "type": "Note",
        "summary": None,
        "inReplyTo": parent,
        "published": published,
        "url": f"{_HOST}/@masto/{status_id}",
        "attributedTo": _ACTOR,
        "to": audience_to,
        "cc": audience_cc,
        "sensitive": False,
        "atomUri": uri,
        "inReplyToAtomUri": parent,
        "conversation": f"tag:{_HOST},2021-05-04:objectId=1:objectType=Conversation",
        "content": content,
        "attachment": [],
        "tag": [],
        **extra,
    }
    return {
        "id": f"{uri}/activity",
        "type": "Create",
        "actor": _ACTOR,
        "published": published,
        "to": audience_to,
        "cc": audience_cc,
        "object": note,
    }


def _announce(
    status_id: str = "2000",
    boosted: str = "https://other.example/users/friend/statuses/9",
    published: str = "2021-06-01T10:00:00Z",
    to: list | None = None,
) -> dict:
    """One Announce activity, shaped like ActivityPub::AnnounceNoteSerializer."""
    return {
        "id": f"{_ACTOR}/statuses/{status_id}/activity",
        "type": "Announce",
        "actor": _ACTOR,
        "published": published,
        "to": [_PUBLIC] if to is None else to,
        "cc": ["https://other.example/users/friend", _FOLLOWERS],
        "object": boosted,
    }


def _document(name: str = "abc.png", **extra) -> dict:
    return {
        "type": "Document",
        "mediaType": "image/png",
        "url": f"media_attachments/files/000/111/222/original/{name}",
        "name": "a red square",
        "blurhash": "U4O:@2%L",
        "width": 2,
        "height": 2,
        **extra,
    }


def _outbox(items: list) -> bytes:
    return json.dumps(
        {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": "outbox.json",
            "type": "OrderedCollection",
            "totalItems": len(items),
            "orderedItems": items,
        }
    ).encode()


def _zip(
    items: list, media: dict[str, bytes] | None = None, actor: bool = True
) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("outbox.json", _outbox(items))
        zf.writestr("likes.json", b'{"orderedItems": []}')
        if actor:
            zf.writestr(
                "actor.json",
                json.dumps({"id": _ACTOR, "followers": _FOLLOWERS}).encode(),
            )
        for name, content in (media or {}).items():
            zf.writestr(f"media_attachments/files/000/111/222/original/{name}", content)
    return buf.getvalue()


@pytest.mark.django_db(databases="__all__")
class TestMastodonImport:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="ma_import@test.com", username="tooter")

    def _run(self, content: bytes, tmp_path, ext="zip"):
        path = tmp_path / f"archive.{ext}"
        path.write_bytes(content)
        task = MastodonImporter.create(self.user, file=str(path))
        task.run()
        return task

    def _posts(self):
        return Post.objects.filter(author_id=self.user.identity.pk).order_by(
            "published"
        )

    def test_import_post_never_federates(self, tmp_path):
        task = self._run(_zip([_note(content="<p>Hello &amp; welcome</p>")]), tmp_path)
        assert task.metadata["imported"] == 1
        post = self._posts().get()
        assert post.state == "fanned_out"
        assert post.local
        assert "Hello &amp; welcome" in post.content
        assert post.content_plain_text.strip() == "Hello & welcome"
        assert post.published == parse_datetime("2021-05-04T09:08:07Z")
        assert post.visibility == Post.Visibilities.public
        assert FanOut.objects.filter(subject_post=post).count() == 0

    def test_recent_post_is_also_quiet(self, tmp_path):
        recent = timezone.now() - datetime.timedelta(hours=1)
        self._run(_zip([_note(published=recent.isoformat())]), tmp_path)
        post = self._posts().get()
        assert post.state == "fanned_out"
        assert FanOut.objects.filter(subject_post=post).count() == 0

    def test_bare_outbox_file(self, tmp_path):
        task = self._run(_outbox([_note()]), tmp_path, ext="json")
        assert task.metadata["imported"] == 1
        assert self._posts().count() == 1

    def test_paragraphs_and_line_breaks_kept(self, tmp_path):
        self._run(
            _zip([_note(content="<p>one<br />two</p><p>three</p>")]),
            tmp_path,
        )
        assert self._posts().get().content_plain_text.strip() == "one\ntwo\n\nthree"

    def test_links_hashtags_and_mentions(self, tmp_path):
        content = (
            '<p>cc <span class="h-card" translate="no">'
            '<a href="https://other.example/@friend" class="u-url mention">'
            "@<span>friend</span></a></span> and "
            '<span class="h-card" translate="no">'
            '<a href="https://mastodon.example/@tooter" class="u-url mention">'
            "@<span>tooter</span></a></span> see "
            '<a href="https://example.org/page?a=1&amp;b=2" target="_blank" '
            'rel="nofollow noopener" translate="no">'
            '<span class="invisible">https://</span>'
            '<span class="ellipsis">example.org/page</span>'
            '<span class="invisible">?a=1&amp;b=2</span></a> '
            '<a href="https://mastodon.example/tags/neodb" class="mention hashtag" '
            'rel="tag">#<span>neodb</span></a></p>'
        )
        tags = [
            {
                "type": "Mention",
                "href": "https://other.example/users/friend",
                "name": "@friend@other.example",
            },
            {
                "type": "Mention",
                "href": "https://mastodon.example/users/tooter",
                "name": "@tooter",
            },
            {
                "type": "Hashtag",
                "href": "https://mastodon.example/tags/neodb",
                "name": "#neodb",
            },
        ]
        self._run(_zip([_note(content=content, tag=tags)]), tmp_path)
        post = self._posts().get()
        text = post.content_plain_text
        assert "https://example.org/page?a=1&b=2" in text
        assert "example.org/page?a=1&amp;b=2" in post.content
        # a handle keeps its domain and never becomes a mention, not even the
        # one that matches a local user of this site
        assert "＠friend@other.example" in text
        assert "＠tooter@mastodon.example" in text
        assert "@friend" not in text
        assert "@tooter" not in text
        assert post.mentions.count() == 0
        assert post.hashtags == ["neodb"]

    def test_boost_becomes_link_post(self, tmp_path):
        task = self._run(_zip([_announce()]), tmp_path)
        assert task.metadata["imported"] == 1
        post = self._posts().get()
        assert post.content_plain_text.strip() == (
            "🔁 https://other.example/users/friend/statuses/9"
        )
        assert post.published == parse_datetime("2021-06-01T10:00:00Z")
        assert post.state == "fanned_out"
        assert post.attachments.count() == 0

    def test_inlined_boost_of_own_post_skipped(self, tmp_path):
        # a boost of one's own followers-only post inlines the note, which is
        # already in the outbox as a Create of its own
        inlined = _announce()
        inlined["object"] = _note(content="<p>mine</p>")["object"]
        task = self._run(_zip([_note(content="<p>mine</p>"), inlined]), tmp_path)
        assert task.metadata["imported"] == 1
        assert self._posts().count() == 1

    @pytest.mark.parametrize(
        "to,cc,expected",
        [
            ([_PUBLIC], [_FOLLOWERS], Post.Visibilities.public),
            ([_FOLLOWERS], [_PUBLIC], Post.Visibilities.unlisted),
            ([_FOLLOWERS], [], Post.Visibilities.followers),
            (["https://other.example/users/friend"], [], Post.Visibilities.mentioned),
        ],
    )
    def test_visibility_from_audience(self, tmp_path, to, cc, expected):
        self._run(_zip([_note(to=to, cc=cc)]), tmp_path)
        assert self._posts().get().visibility == expected

    def test_followers_only_without_actor_file(self, tmp_path):
        # a bare outbox has no followers collection to compare against
        task = self._run(_outbox([_note(to=[_FOLLOWERS], cc=[])]), tmp_path, ext="json")
        assert task.metadata["imported"] == 1
        assert self._posts().get().visibility == Post.Visibilities.followers

    def test_account_default_does_not_change_visibility(self, tmp_path):
        # only the archive decides: a public post is not demoted to the
        # posting default of the account
        self.user.preference.post_public_mode = 1  # unlisted
        self.user.preference.save(update_fields=["post_public_mode"])
        self._run(
            _zip(
                [
                    _note("1000"),
                    _note("1001", published="2021-05-04T09:09:00Z", to=[], cc=[]),
                ]
            ),
            tmp_path,
        )
        public, direct = list(self._posts())
        assert public.visibility == Post.Visibilities.public
        assert direct.visibility == Post.Visibilities.mentioned

    def test_self_reply_threaded(self, tmp_path):
        self._run(
            _zip(
                [
                    _note(
                        "1001",
                        "<p>second</p>",
                        published="2021-05-04T09:10:00Z",
                        reply_to="1000",
                    ),
                    _note("1000", "<p>first</p>"),
                ]
            ),
            tmp_path,
        )
        first, second = list(self._posts())
        assert "first" in first.content
        assert second.in_reply_to == first.object_uri
        assert second.state == "fanned_out"

    def test_thread_posted_in_one_second(self, tmp_path):
        when = "2022-08-08T15:14:12Z"
        archive = _zip(
            [
                _note("3000", "<p>first part of a thread</p>", published=when),
                _note(
                    "3001",
                    "<p>second part of a thread</p>",
                    published=when,
                    reply_to="3000",
                ),
            ]
        )
        task = self._run(archive, tmp_path)
        assert task.metadata["imported"] == 2
        first, second = list(self._posts().order_by("id"))
        assert "first part" in first.content
        assert second.in_reply_to == first.object_uri
        assert first.published == parse_datetime("2022-08-08T15:14:12Z")
        assert (
            first.published < second.published < parse_datetime("2022-08-08T15:14:13Z")
        )
        task = self._run(archive, tmp_path)
        assert task.metadata["skipped"] == 2
        assert self._posts().count() == 2

    def test_reply_threads_onto_post_of_earlier_run(self, tmp_path):
        self._run(_zip([_note("1000", "<p>first</p>")]), tmp_path)
        self._run(
            _zip(
                [
                    _note("1000", "<p>first</p>"),
                    _note(
                        "1001",
                        "<p>second</p>",
                        published="2021-05-04T09:10:00Z",
                        reply_to="1000",
                    ),
                ]
            ),
            tmp_path,
        )
        first, second = list(self._posts())
        assert second.in_reply_to == first.object_uri

    def test_reply_to_other_is_plain_post(self, tmp_path):
        note = _note()
        note["object"]["inReplyTo"] = "https://other.example/users/friend/statuses/7"
        note["object"]["inReplyToAtomUri"] = note["object"]["inReplyTo"]
        self._run(_zip([note]), tmp_path)
        post = self._posts().get()
        assert post.in_reply_to is None

    def test_import_is_idempotent(self, tmp_path):
        archive = _zip(
            [_note(), _note("1001", "<p>two</p>", published="2021-05-04T09:09:00Z")]
        )
        self._run(archive, tmp_path)
        task = self._run(archive, tmp_path)
        assert task.metadata["imported"] == 0
        assert task.metadata["skipped"] == 2
        assert self._posts().count() == 2

    def test_image_attached(self, tmp_path):
        self._run(
            _zip(
                [_note(attachment=[_document()])],
                media={"abc.png": _png()},
            ),
            tmp_path,
        )
        attachment = self._posts().get().attachments.get()
        assert attachment.mimetype == "image/png"
        assert attachment.name == "a red square"

    def test_image_found_by_name_when_path_differs(self, tmp_path):
        # an instance on object storage exports a path of its own shape
        document = _document(url="/system/media_attachments/files/abc.png")
        task = self._run(
            _zip([_note(attachment=[document])], media={"abc.png": _png()}),
            tmp_path,
        )
        assert task.metadata["imported"] == 1
        assert self._posts().get().attachments.count() == 1

    def test_video_missing_file_and_preview_card_skipped(self, tmp_path):
        attachment = [
            _document("v.mp4", mediaType="video/mp4"),
            _document("gone.png"),
            {"type": "Link", "href": "https://example.org/article"},
        ]
        task = self._run(_zip([_note(attachment=attachment)]), tmp_path)
        assert task.metadata["imported"] == 1
        assert self._posts().get().attachments.count() == 0

    def test_media_only_post_is_imported(self, tmp_path):
        task = self._run(
            _zip(
                [_note(content="<p></p>", attachment=[_document()])],
                media={"abc.png": _png()},
            ),
            tmp_path,
        )
        assert task.metadata["imported"] == 1
        assert self._posts().get().attachments.count() == 1

    def test_content_warning_sensitive_and_language(self, tmp_path):
        self._run(
            _zip(
                [
                    _note(
                        content="<p>剧透</p>",
                        summary="spoilers",
                        sensitive=True,
                        contentMap={"zh-CN": "<p>剧透</p>"},
                    )
                ]
            ),
            tmp_path,
        )
        post = self._posts().get()
        assert post.summary == "spoilers"
        assert post.sensitive
        assert post.language == "zh"

    def test_poll_options_listed(self, tmp_path):
        note = _note(
            content="<p>tea or coffee?</p>",
            type="Question",
            oneOf=[
                {"type": "Note", "name": "tea", "replies": {"totalItems": 2}},
                {"type": "Note", "name": "coffee", "replies": {"totalItems": 5}},
            ],
        )
        task = self._run(_zip([note]), tmp_path)
        assert task.metadata["imported"] == 1
        text = self._posts().get().content_plain_text
        assert "tea or coffee?" in text
        assert "- tea" in text
        assert "- coffee" in text

    def test_empty_post_and_unknown_activity_skipped(self, tmp_path):
        task = self._run(
            _zip(
                [
                    _note(content="<p></p>"),
                    {"type": "Like", "object": "https://other.example/statuses/1"},
                ]
            ),
            tmp_path,
        )
        assert task.metadata["imported"] == 0
        assert self._posts().count() == 0


@pytest.mark.django_db(databases="__all__")
class TestMastodonImportView:
    def test_upload_creates_task(self, client, tmp_path, settings, monkeypatch):
        settings.MEDIA_ROOT = str(tmp_path)
        user = User.register(email="ma_view@test.com", username="ma_viewer")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        enqueued = []
        monkeypatch.setattr(
            MastodonImporter, "enqueue", lambda self: enqueued.append(self.pk)
        )
        upload = SimpleUploadedFile("archive.zip", _zip([_note()]))
        response = client.post(reverse("users:import_mastodon"), {"file": upload})
        assert response.status_code == 302
        task = MastodonImporter.latest_task(user)
        assert task is not None
        assert enqueued == [task.pk]
        assert task.metadata["file"].endswith(".zip")
        with open(task.metadata["file"], "rb") as f:
            assert zipfile.is_zipfile(f)

    def test_bare_outbox_upload_kept_as_json(
        self, client, tmp_path, settings, monkeypatch
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        monkeypatch.setattr(MastodonImporter, "enqueue", lambda self: None)
        user = User.register(email="ma_view6@test.com", username="ma_viewer6")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        upload = SimpleUploadedFile("outbox.json", _outbox([_note()]))
        response = client.post(reverse("users:import_mastodon"), {"file": upload})
        assert response.status_code == 302
        task = MastodonImporter.latest_task(user)
        assert task is not None
        assert task.metadata["file"].endswith(".json")

    def test_invalid_upload_rejected(self, client):
        user = User.register(email="ma_view2@test.com", username="ma_viewer2")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        upload = SimpleUploadedFile("junk.zip", b"not an archive")
        response = client.post(reverse("users:import_mastodon"), {"file": upload})
        assert response.status_code == 400
        assert MastodonImporter.latest_task(user) is None

    def test_data_page_hides_section_by_default(self, client):
        user = User.register(email="ma_view3@test.com", username="ma_viewer3")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(reverse("users:data"))
        assert response.status_code == 200
        assert reverse("users:import_mastodon") not in response.content.decode()

    def test_data_page_shows_section_when_enabled(self, client, monkeypatch):
        enabled = SiteConfig.system.model_copy(update={"enable_import_mastodon": True})
        monkeypatch.setattr(SiteConfig, "system", enabled)
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        user = User.register(email="ma_view4@test.com", username="ma_viewer4")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(reverse("users:data"))
        assert response.status_code == 200
        assert reverse("users:import_mastodon") in response.content.decode()

    def test_upload_works_without_the_switch(
        self, client, tmp_path, settings, monkeypatch
    ):
        # the switch only hides the UI; the endpoint itself is not gated
        settings.MEDIA_ROOT = str(tmp_path)
        monkeypatch.setattr(MastodonImporter, "enqueue", lambda self: None)
        user = User.register(email="ma_view5@test.com", username="ma_viewer5")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        upload = SimpleUploadedFile("archive.zip", _zip([_note()]))
        response = client.post(reverse("users:import_mastodon"), {"file": upload})
        assert response.status_code == 302
        assert MastodonImporter.latest_task(user) is not None


@pytest.mark.django_db(databases="__all__")
class TestMastodonValidateFile:
    def test_accepts_zip(self):
        assert MastodonImporter.validate_file(BytesIO(_zip([_note()])))

    def test_accepts_bare_outbox(self):
        assert MastodonImporter.validate_file(BytesIO(_outbox([_note()])))

    def test_accepts_bare_outbox_behind_a_long_context(self):
        # the real archive lists every context extension before the items
        outbox = json.loads(_outbox([_note()]))
        outbox["@context"] = [f"https://example.org/ns/{i}" for i in range(500)]
        raw = json.dumps(outbox).encode()
        assert raw.index(b"orderedItems") > 8192
        assert MastodonImporter.validate_file(BytesIO(raw))

    def test_rejects_zip_without_outbox(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("likes.json", b'{"orderedItems": []}')
        assert not MastodonImporter.validate_file(BytesIO(buf.getvalue()))

    def test_rejects_junk(self):
        assert not MastodonImporter.validate_file(BytesIO(b"[1, 2, 3]"))
        assert not MastodonImporter.validate_file(BytesIO(b'{"type": "Person"}'))
        assert not MastodonImporter.validate_file(None)
