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
from journal.importers import TwitterImporter
from takahe.models import FanOut, Post
from users.models import User


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def _tweet(
    tweet_id: str = "1000",
    text: str = "Hello world",
    created_at: str = "Tue May 04 09:08:07 +0000 2021",
    urls: list | None = None,
    media: list | None = None,
    reply_to: str | None = None,
    **extra,
) -> dict:
    tweet = {
        "id_str": tweet_id,
        "id": tweet_id,
        "full_text": text,
        "created_at": created_at,
        "lang": "en",
        "entities": {"urls": urls or [], "user_mentions": [], "hashtags": []},
        **extra,
    }
    if media:
        tweet["entities"]["media"] = media
        tweet["extended_entities"] = {"media": media}
    if reply_to:
        tweet["in_reply_to_status_id_str"] = reply_to
        tweet["in_reply_to_status_id"] = reply_to
    return {"tweet": tweet}


def _photo(tweet_id: str, name: str = "abc.png") -> dict:
    return {
        "type": "photo",
        "url": "https://t.co/media1",
        "media_url_https": f"https://pbs.twimg.com/media/{name}",
        "media_url": f"http://pbs.twimg.com/media/{name}",
        "ext_alt_text": "a red square",
    }


def _js(tweets: list, kind: str = "tweets") -> bytes:
    return f"window.YTD.{kind}.part0 = ".encode() + json.dumps(tweets).encode()


def _zip(
    tweets: list, notes: list | None = None, media: dict[str, bytes] | None = None
) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data/tweets.js", _js(tweets))
        zf.writestr("data/account.js", b"window.YTD.account.part0 = []")
        if notes is not None:
            zf.writestr("data/note-tweet.js", _js(notes, "note_tweet"))
        for name, content in (media or {}).items():
            zf.writestr(f"data/tweets_media/{name}", content)
    return buf.getvalue()


@pytest.mark.django_db(databases="__all__")
class TestTwitterImport:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tw_import@test.com", username="tweeter")

    def _run(self, content: bytes, tmp_path, visibility: int = 0, ext="zip"):
        path = tmp_path / f"archive.{ext}"
        path.write_bytes(content)
        task = TwitterImporter.create(self.user, visibility=visibility, file=str(path))
        task.run()
        return task

    def _posts(self):
        return Post.objects.filter(author_id=self.user.identity.pk).order_by(
            "published"
        )

    def test_import_tweet_never_federates(self, tmp_path):
        task = self._run(_zip([_tweet(text="Hello &amp; welcome")]), tmp_path)
        assert task.metadata["imported"] == 1
        post = self._posts().get()
        assert post.state == "fanned_out"
        assert post.local
        assert "Hello &amp; welcome" in post.content
        assert post.published == parse_datetime("2021-05-04T09:08:07Z")
        assert post.visibility == Post.Visibilities.public
        assert FanOut.objects.filter(subject_post=post).count() == 0

    def test_recent_tweet_is_also_quiet(self, tmp_path):
        recent = (timezone.now() - datetime.timedelta(hours=1)).strftime(
            "%a %b %d %H:%M:%S +0000 %Y"
        )
        self._run(_zip([_tweet(created_at=recent)]), tmp_path)
        post = self._posts().get()
        assert post.state == "fanned_out"
        assert FanOut.objects.filter(subject_post=post).count() == 0

    @pytest.mark.parametrize(
        "visibility,expected",
        [
            (0, Post.Visibilities.public),
            (1, Post.Visibilities.followers),
            (2, Post.Visibilities.mentioned),
        ],
    )
    def test_visibility(self, tmp_path, visibility, expected):
        self._run(_zip([_tweet()]), tmp_path, visibility=visibility)
        assert self._posts().get().visibility == expected

    def test_bare_js_file(self, tmp_path):
        task = self._run(_js([_tweet()]), tmp_path, ext="js")
        assert task.metadata["imported"] == 1
        assert self._posts().count() == 1

    def test_links_expanded_and_handles_not_mentioned(self, tmp_path):
        self._run(
            _zip(
                [
                    _tweet(
                        text="cc @tweeter see https://t.co/abc #neodb",
                        urls=[
                            {
                                "url": "https://t.co/abc",
                                "expanded_url": "https://example.org/page?a=1&b=2",
                            }
                        ],
                    )
                ]
            ),
            tmp_path,
        )
        post = self._posts().get()
        assert "t.co" not in post.content
        assert "https://example.org/page?a=1&amp;b=2" in post.content
        assert "＠tweeter" in post.content
        assert "@tweeter" not in post.content
        # the handle matches a local user but must not become a mention
        assert post.mentions.count() == 0
        assert post.hashtags == ["neodb"]

    def test_retweet_becomes_link_post(self, tmp_path):
        task = self._run(
            _zip(
                [
                    _tweet(
                        "1847061695962763653",
                        "RT @bluesky: Bluesky is an open social network that gives…",
                    )
                ]
            ),
            tmp_path,
        )
        assert task.metadata["imported"] == 1
        post = self._posts().get()
        assert post.content_plain_text == (
            "RT ＠bluesky https://x.com/bluesky/status/1847061695962763653"
        )
        assert post.mentions.count() == 0
        assert post.state == "fanned_out"

    def test_retweet_with_original_links_to_it(self, tmp_path):
        original = {"id_str": "555", "user": {"screen_name": "someone"}}
        self._run(
            _zip([_tweet(text="RT @someone: their words", retweeted_status=original)]),
            tmp_path,
        )
        post = self._posts().get()
        assert "https://x.com/someone/status/555" in post.content

    def test_self_reply_threaded(self, tmp_path):
        self._run(
            _zip(
                [
                    _tweet(
                        "2000",
                        "second",
                        "Tue May 04 09:10:00 +0000 2021",
                        reply_to="1000",
                    ),
                    _tweet("1000", "first"),
                ]
            ),
            tmp_path,
        )
        first, second = list(self._posts())
        assert "first" in first.content
        assert second.in_reply_to == first.object_uri
        assert second.state == "fanned_out"

    def test_thread_posted_in_one_second(self, tmp_path):
        when = "Mon Aug 08 15:14:12 +0000 2022"
        archive = _zip(
            [
                _tweet("3000", "first part of a thread", when),
                _tweet("3001", "second part of a thread", when, reply_to="3000"),
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

    def test_snowflake_collision_is_retried(self, tmp_path, monkeypatch):
        from takahe.models import Snowflake

        real = Snowflake.generate_post_at
        fixed = real(
            datetime.datetime(2021, 5, 4, 9, 8, 7, tzinfo=datetime.UTC).timestamp()
        )
        calls = []

        def colliding(t: float) -> int:
            calls.append(t)
            # first post takes the fixed id, second post draws it again once
            return fixed if len(calls) <= 2 else real(t)

        monkeypatch.setattr(Snowflake, "generate_post_at", staticmethod(colliding))
        task = self._run(
            _zip([_tweet(), _tweet("1001", "two", "Tue May 04 09:09:00 +0000 2021")]),
            tmp_path,
        )
        assert task.metadata["imported"] == 2
        assert task.metadata["failed"] == 0
        assert len(calls) == 3
        assert self._posts().filter(pk=fixed).exists()

    def test_reply_to_other_is_plain_post(self, tmp_path):
        self._run(_zip([_tweet(text="@other yes", reply_to="42")]), tmp_path)
        post = self._posts().get()
        assert post.in_reply_to is None
        assert post.mentions.count() == 0

    def test_import_is_idempotent(self, tmp_path):
        archive = _zip(
            [_tweet(), _tweet("1001", "two", "Tue May 04 09:09:00 +0000 2021")]
        )
        self._run(archive, tmp_path)
        task = self._run(archive, tmp_path)
        assert task.metadata["imported"] == 0
        assert task.metadata["skipped"] == 2
        assert self._posts().count() == 2

    def test_photo_attached(self, tmp_path):
        media = {"1000-abc.png": _png()}
        self._run(
            _zip(
                [_tweet(text="look https://t.co/media1", media=[_photo("1000")])],
                media=media,
            ),
            tmp_path,
        )
        post = self._posts().get()
        assert "t.co" not in post.content
        attachment = post.attachments.get()
        assert attachment.mimetype == "image/png"
        assert attachment.name == "a red square"

    def test_missing_media_and_video_skipped(self, tmp_path):
        video = {**_photo("1000", "v.mp4"), "type": "video"}
        task = self._run(
            _zip(
                [_tweet(text="pic https://t.co/media1", media=[_photo("1000"), video])]
            ),
            tmp_path,
        )
        assert task.metadata["imported"] == 1
        assert self._posts().get().attachments.count() == 0

    def test_note_tweet_replaces_truncated_text(self, tmp_path):
        long_text = "This is the whole long story. " * 20
        truncated = long_text[:100] + "… https://t.co/self"
        tweets = [
            _tweet(
                text=truncated,
                created_at="Wed Feb 08 15:12:34 +0000 2023",
                urls=[
                    {
                        "url": "https://t.co/self",
                        "expanded_url": "https://twitter.com/i/web/status/1000",
                    }
                ],
            )
        ]
        notes = [
            {
                "noteTweet": {
                    "noteTweetId": "77",
                    "createdAt": "2023-02-08T15:12:34.000Z",
                    "core": {"text": long_text.strip(), "urls": [], "mentions": []},
                }
            }
        ]
        task = self._run(_zip(tweets, notes=notes), tmp_path)
        assert task.metadata["imported"] == 1
        post = self._posts().get()
        assert "…" not in post.content
        assert "t.co" not in post.content
        assert "twitter.com/i/web" not in post.content
        assert post.content.count("whole long story") == 20

    def test_note_tweet_needs_matching_text(self, tmp_path):
        # a different tweet in the same second must keep its own text
        tweets = [
            _tweet(
                text="Unrelated short tweet",
                created_at="Wed Feb 08 15:12:34 +0000 2023",
            )
        ]
        notes = [
            {
                "noteTweet": {
                    "noteTweetId": "77",
                    "createdAt": "2023-02-08T15:12:34.000Z",
                    "core": {"text": "A long note about something else", "urls": []},
                }
            }
        ]
        self._run(_zip(tweets, notes=notes), tmp_path)
        post = self._posts().get()
        assert "Unrelated short tweet" in post.content
        assert "something else" not in post.content

    def test_sensitive_flag_and_language(self, tmp_path):
        self._run(
            _zip([_tweet(possibly_sensitive=True, lang="und")]),
            tmp_path,
        )
        post = self._posts().get()
        assert post.sensitive
        assert post.language == ""


@pytest.mark.django_db(databases="__all__")
class TestTwitterImportView:
    def test_upload_creates_task(self, client, tmp_path, settings, monkeypatch):
        settings.MEDIA_ROOT = str(tmp_path)
        user = User.register(email="tw_view@test.com", username="tw_viewer")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        enqueued = []
        monkeypatch.setattr(
            TwitterImporter, "enqueue", lambda self: enqueued.append(self.pk)
        )
        upload = SimpleUploadedFile("twitter.zip", _zip([_tweet()]))
        response = client.post(
            reverse("users:import_twitter"), {"file": upload, "visibility": "1"}
        )
        assert response.status_code == 302
        task = TwitterImporter.latest_task(user)
        assert task is not None
        assert enqueued == [task.pk]
        assert task.metadata["visibility"] == 1
        assert task.metadata["file"].endswith(".zip")
        with open(task.metadata["file"], "rb") as f:
            assert zipfile.is_zipfile(f)

    def test_invalid_upload_rejected(self, client):
        user = User.register(email="tw_view2@test.com", username="tw_viewer2")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        upload = SimpleUploadedFile("junk.zip", b"not an archive")
        response = client.post(
            reverse("users:import_twitter"), {"file": upload, "visibility": "0"}
        )
        assert response.status_code == 400
        assert TwitterImporter.latest_task(user) is None

    def test_data_page_hides_section_by_default(self, client):
        user = User.register(email="tw_view3@test.com", username="tw_viewer3")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(reverse("users:data"))
        assert response.status_code == 200
        assert reverse("users:import_twitter") not in response.content.decode()

    def test_data_page_shows_section_when_enabled(self, client, monkeypatch):
        enabled = SiteConfig.system.model_copy(update={"enable_import_twitter": True})
        monkeypatch.setattr(SiteConfig, "system", enabled)
        monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
        user = User.register(email="tw_view4@test.com", username="tw_viewer4")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(reverse("users:data"))
        assert response.status_code == 200
        assert reverse("users:import_twitter") in response.content.decode()

    def test_upload_works_without_the_switch(
        self, client, tmp_path, settings, monkeypatch
    ):
        # the switch only hides the UI; the endpoint itself is not gated
        settings.MEDIA_ROOT = str(tmp_path)
        monkeypatch.setattr(TwitterImporter, "enqueue", lambda self: None)
        user = User.register(email="tw_view5@test.com", username="tw_viewer5")
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        upload = SimpleUploadedFile("twitter.zip", _zip([_tweet()]))
        response = client.post(
            reverse("users:import_twitter"), {"file": upload, "visibility": "0"}
        )
        assert response.status_code == 302
        assert TwitterImporter.latest_task(user) is not None


@pytest.mark.django_db(databases="__all__")
class TestTwitterValidateFile:
    def test_accepts_zip(self):
        assert TwitterImporter.validate_file(BytesIO(_zip([_tweet()])))

    def test_accepts_js(self):
        assert TwitterImporter.validate_file(BytesIO(_js([_tweet()])))

    def test_rejects_zip_without_tweets(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("data/account.js", b"window.YTD.account.part0 = []")
        assert not TwitterImporter.validate_file(BytesIO(buf.getvalue()))

    def test_rejects_junk(self):
        assert not TwitterImporter.validate_file(BytesIO(b"[1, 2, 3]"))
        assert not TwitterImporter.validate_file(None)
