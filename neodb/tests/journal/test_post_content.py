"""Tests for ~neodb~ placeholder URL rewriting in web post rendering.

Posts federated by NeoDB embed item links as
``{site_url}/~neodb~{item_url}`` so consuming instances can localize
them. The takahe app rewrites these for its own templates and the
Mastodon API; the mirror ``takahe.models.Post`` must do the same for
NeoDB web templates rendering ``safe_content_local``.
"""

from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

import pytest
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.safestring import SafeString

from catalog.models import Edition
from journal.models import Mark, ShelfType
from takahe import html as neodb_html
from takahe.html import FediverseHtmlParser
from takahe.models import Post
from takahe.utils import Takahe
from users.models import User


class TestRewriteNeodbUrls:
    def test_rewrites_remote_placeholder_href(self):
        content = '<a href="https://remote.example/~neodb~/movie/abc">Title</a>'
        result = Post._rewrite_neodb_urls(content)
        assert result == (
            '<a href="https://example.org/search?r=1&q=https%3A%2F%2Fremote.example%2Fmovie%2Fabc">Title</a>'
        )

    def test_result_stays_html_safe(self):
        # templates render safe_content_local without |safe, so the rewrite
        # must not strip the SafeString marker set by ContentRenderer
        assert isinstance(Post._rewrite_neodb_urls("<p>hi</p>"), SafeString)

    def test_leaves_plain_links_unchanged(self):
        content = '<a href="https://remote.example/movie/abc">Title</a>'
        assert Post._rewrite_neodb_urls(content) == content


@pytest.mark.django_db(databases="__all__")
def test_safe_content_local_rewrites_item_link():
    book = Edition.objects.create(title="Rewrite Test Book")
    user = User.register(email="rewrite@test.com", username="rewrite_user")
    Mark(user.identity, book).update(ShelfType.WISHLIST, "note", None, [], 0)
    shelfmember = Mark(user.identity, book).shelfmember
    assert shelfmember is not None
    post = shelfmember.latest_post
    assert post is not None
    assert f"/~neodb~{book.url}" in post.content
    rewritten_href = (
        'href="https://example.org/search?r=1&q='
        f'{quote(f"https://example.org{book.url}", safe="")}"'
    )
    rendered = post.safe_content_local
    assert "~neodb~" not in rendered
    assert rewritten_href in rendered

    # the rewritten anchor must reach page HTML unescaped
    response = Client().get(
        reverse(
            "journal:user_post_list",
            kwargs={"user_name": user.identity.handle},
        )
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert rewritten_href in html
    assert "~neodb~" not in html


class TestMirrorParser:
    """The neodb mirror renders the web UI, so its own copy needs coverage.

    takahe/core/html.py is the file the takahe suite exercises; these run the
    same paths through the neodb import so the mirror cannot rot unnoticed.
    """

    def test_keeps_lists_quotes_and_code(self):
        parser = FediverseHtmlParser(
            "<p>a</p><ul><li>one</li><li>two</li></ul>"
            "<blockquote><p>q</p></blockquote><pre><code>x = 1\ny = 2</code></pre>"
        )
        assert parser.html == (
            "<p>a</p><ul><li>one</li><li>two</li></ul>"
            "<blockquote><p>q</p></blockquote><pre><code>x = 1\ny = 2</code></pre>"
        )
        assert parser.plain_text == "a\n\none\ntwo\n\nq\n\n\n\nx = 1\ny = 2"

    def test_demotes_headings_and_keeps_inline(self):
        parser = FediverseHtmlParser(
            "<h3>T</h3><p><strong>b</strong><em>i</em><code>c</code><del>d</del></p>"
        )
        assert parser.html == (
            "<p><strong>T</strong></p>"
            "<p><strong>b</strong><em>i</em><code>c</code><del>d</del></p>"
        )

    def test_balances_unclosed_remote_markup(self):
        assert FediverseHtmlParser("<p>a</p><pre>rest").html == (
            "<p>a</p><pre>rest</pre>"
        )
        assert FediverseHtmlParser("<ul><li>a<li>b").html == (
            "<ul><li>a</li><li>b</li></ul>"
        )
        assert FediverseHtmlParser("</ul></p><p>ok</p>").html == "<p>ok</p>"

    def test_does_not_linkify_inside_code(self):
        parser = FediverseHtmlParser(
            "<pre><code>#tag https://example.com/x</code></pre>", find_hashtags=True
        )
        assert parser.html == "<pre><code>#tag https://example.com/x</code></pre>"
        assert parser.hashtags == set()

    def test_drops_attributes_and_unknown_tags(self):
        parser = FediverseHtmlParser('<ul onclick="evil()"><li class="x">y</li></ul>')
        assert parser.html == "<ul><li>y</li></ul>"
        assert FediverseHtmlParser("<table><tr><td>c</td></tr></table>").html == "c"

    def test_mirror_matches_takahe_copy(self):
        """Only the Emoji import may differ, or the two renderers diverge."""
        mirror = Path(neodb_html.__file__)
        # repo root, whether run from a checkout or the dev container mounts
        original = mirror.parents[2] / "takahe" / "core" / "html.py"
        if not original.exists():
            pytest.skip(f"takahe checkout not present at {original}")
        neodb_src = mirror.read_text()
        normalized = original.read_text().replace(
            "from activities.models import Emoji", "from .models import Emoji"
        )
        assert neodb_src == normalized, (
            "neodb/takahe/html.py and takahe/core/html.py have drifted; "
            "keep them identical apart from the Emoji import"
        )


@pytest.mark.django_db(databases="__all__")
class TestPostWritingViews:
    @pytest.fixture(autouse=True)
    def setup_user(self, client: Client) -> None:
        self.owner = User.register(username="writer")
        client.force_login(self.owner)

    @pytest.fixture
    def original_post(self) -> Post:
        post = Takahe.post(
            self.owner.identity.pk, "Original", Takahe.Visibilities.public
        )
        assert post is not None
        return post

    @pytest.fixture
    def other_writer(self) -> User:
        return User.register(username="otherwriter")

    def test_anonymous_cannot_compose(self, client: Client) -> None:
        client.logout()
        response = client.post(reverse("journal:post_compose"), {"content": "New"})
        assert response.status_code == 302
        assert reverse("users:login") in response["Location"]
        assert not Post.objects.exists()

    def test_anonymous_cannot_edit(self, client: Client, original_post: Post) -> None:
        client.logout()
        response = client.post(
            reverse("journal:post_edit", args=[original_post.pk]), {"content": "New"}
        )
        assert response.status_code == 302
        assert reverse("users:login") in response["Location"]
        assert Post.objects.count() == 1
        original_post.refresh_from_db()
        assert original_post.content_plain_text == "Original"

    def test_compose_form_and_submission_preserve_options(self, client: Client) -> None:
        url = reverse("journal:post_compose")
        form = client.get(url)
        assert form.status_code == 200
        assert form.context["visibility"] == self.owner.preference.post_public_mode
        response = client.post(
            url,
            {
                "content": "  A new post  ",
                "visibility": "1",
                "sensitive": "on",
                "subject": "  Spoilers  ",
                "language": "fr",
            },
            HTTP_REFERER="https://example.org/feed/",
        )
        assert response.status_code == 302
        assert response["Location"] == "https://example.org/feed/"
        created = Post.objects.get()
        assert created.author_id == self.owner.identity.pk
        assert created.content_plain_text == "A new post"
        assert created.visibility == Takahe.Visibilities.followers
        assert created.sensitive is True
        assert created.summary == "Spoilers"
        assert created.language == "fr"

    def test_empty_content_cannot_create(self, client: Client) -> None:
        response = client.post(reverse("journal:post_compose"), {"content": " \n "})
        assert response.status_code == 400
        assert not Post.objects.exists()

    def test_empty_content_cannot_edit(
        self, client: Client, original_post: Post
    ) -> None:
        response = client.post(
            reverse("journal:post_edit", args=[original_post.pk]), {"content": " \n "}
        )
        assert response.status_code == 400
        assert Post.objects.count() == 1
        original_post.refresh_from_db()
        assert original_post.content_plain_text == "Original"

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (0, Takahe.Visibilities.public),
            (1, Takahe.Visibilities.unlisted),
            (4, Takahe.Visibilities.local_only),
        ],
    )
    @pytest.mark.parametrize("visibility", [None, "invalid"])
    def test_compose_uses_default_visibility_and_safe_redirect(
        self, client: Client, mode: int, expected: int, visibility: str | None
    ) -> None:
        self.owner.preference.post_public_mode = mode
        self.owner.preference.save()
        form = client.get(reverse("journal:post_compose"))
        assert form.context["visibility"] == 0
        data = {
            "content": "New post",
            "subject": "Ignored without sensitive",
            "language": "x",
        }
        if visibility is not None:
            data["visibility"] = visibility
        response = client.post(
            reverse("journal:post_compose"),
            data,
            HTTP_REFERER="https://untrusted.example/",
        )
        assert response.status_code == 302
        assert not response["Location"].startswith("https://untrusted.example/")
        created = Post.objects.get()
        assert created.visibility == expected
        assert created.summary is None
        assert created.language == ""

    def test_failed_image_upload_still_posts_text(self, client: Client) -> None:
        image = SimpleUploadedFile("image.png", b"image", content_type="image/png")
        with patch.object(Takahe, "upload_image", side_effect=ValueError("bad image")):
            response = client.post(
                reverse("journal:post_compose"),
                {"content": "Text survives", "image_0": image},
            )
        assert response.status_code == 302
        created = Post.objects.get()
        assert created.content_plain_text == "Text survives"
        assert not created.attachments.exists()

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_other_user_cannot_edit(
        self, client: Client, original_post: Post, other_writer: User, method: str
    ) -> None:
        client.force_login(other_writer)
        response = getattr(client, method)(
            reverse("journal:post_edit", args=[original_post.pk]),
            {"content": "Hijacked"},
        )
        assert response.status_code == 403
        original_post.refresh_from_db()
        assert original_post.content_plain_text == "Original"

    @pytest.mark.parametrize("state", ["deleted", "deleted_fanned_out"])
    def test_deleted_post_cannot_be_edited(
        self, client: Client, original_post: Post, state: str
    ) -> None:
        Post.objects.filter(pk=original_post.pk).update(state=state)
        response = client.post(
            reverse("journal:post_edit", args=[original_post.pk]),
            {"content": "Revived"},
        )
        assert response.status_code == 404
        original_post.refresh_from_db()
        assert original_post.state == state
        assert original_post.content_plain_text == "Original"

    def test_piece_post_uses_its_own_editor(self, client: Client) -> None:
        item = Edition.objects.create(title="Linked book")
        Mark(self.owner.identity, item).update(
            ShelfType.WISHLIST, "Book note", visibility=0
        )
        member = Mark(self.owner.identity, item).shelfmember
        assert member is not None and member.latest_post is not None
        response = client.post(
            reverse("journal:post_edit", args=[member.latest_post.pk]),
            {"content": "Replacement"},
        )
        assert response.status_code == 403
        member.latest_post.refresh_from_db()
        assert "Replacement" not in member.latest_post.content_plain_text

    def test_owner_edit_keeps_visibility_and_post_id(
        self, client: Client, original_post: Post
    ) -> None:
        Post.objects.filter(pk=original_post.pk).update(
            visibility=Takahe.Visibilities.followers
        )
        url = reverse("journal:post_edit", args=[original_post.pk])
        form = client.get(url)
        assert form.status_code == 200
        assert form.context["content"] == "Original"
        response = client.post(
            url,
            {
                "content": "Edited",
                "visibility": "0",
                "sensitive": "true",
                "subject": "Warning",
                "language": "x",
            },
        )
        assert response.status_code == 302
        assert Post.objects.count() == 1
        original_post.refresh_from_db()
        assert original_post.content_plain_text == "Edited"
        assert original_post.visibility == Takahe.Visibilities.followers
        assert original_post.summary == "Warning"
        assert original_post.sensitive is True
        assert original_post.language == ""

    @pytest.mark.parametrize("visibility", [0, 1, 2, 3, 4])
    def test_quote_can_keep_original_visibility(
        self, client: Client, original_post: Post, visibility: int
    ) -> None:
        Post.objects.filter(pk=original_post.pk).update(visibility=visibility)
        response = client.post(
            reverse("journal:post_quote", args=[original_post.pk]),
            {"content": "A quote", "visibility": str(visibility)},
        )
        assert response.status_code == 200
        quote_post = Post.objects.exclude(pk=original_post.pk).get()
        assert quote_post.quote_url == original_post.object_uri
        assert quote_post.visibility == visibility
        assert quote_post.author_id == self.owner.identity.pk

    @pytest.mark.parametrize(
        ("original", "submitted"), [(1, 0), (2, 0), (3, 2), (4, 1)]
    )
    def test_quote_cannot_widen_visibility(
        self, client: Client, original_post: Post, original: int, submitted: int
    ) -> None:
        Post.objects.filter(pk=original_post.pk).update(visibility=original)
        response = client.post(
            reverse("journal:post_quote", args=[original_post.pk]),
            {"content": "A quote", "visibility": str(submitted)},
        )
        assert response.status_code == 400
        assert Post.objects.count() == 1

    @pytest.mark.parametrize("visibility", [2, 3])
    def test_unrelated_user_cannot_quote_private_post(
        self, client: Client, original_post: Post, other_writer: User, visibility: int
    ) -> None:
        client.force_login(other_writer)
        Post.objects.filter(pk=original_post.pk).update(visibility=visibility)
        response = client.post(
            reverse("journal:post_quote", args=[original_post.pk]),
            {"content": "A quote", "visibility": str(visibility)},
        )
        assert response.status_code == 403
        assert Post.objects.count() == 1

    def test_anonymous_can_read_public_quotes_but_cannot_post(
        self, client: Client, original_post: Post
    ) -> None:
        client.logout()
        url = reverse("journal:post_quote", args=[original_post.pk])
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["allowed_visibilities"] == []
        assert (
            client.post(url, {"content": "A quote", "visibility": "0"}).status_code
            == 403
        )
        assert Post.objects.count() == 1

    @pytest.mark.parametrize(
        "data",
        [{"content": "", "visibility": "0"}, {"content": "Quote", "visibility": "bad"}],
    )
    def test_invalid_quote_does_not_post(
        self, client: Client, original_post: Post, data: dict[str, str]
    ) -> None:
        response = client.post(
            reverse("journal:post_quote", args=[original_post.pk]), data
        )
        assert response.status_code == 400
        assert Post.objects.count() == 1
