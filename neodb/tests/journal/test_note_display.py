from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from catalog.models import Edition
from journal.models import Note
from journal.models.renderers import spoiler_warning
from social.templatetags.social_posts import note_content_parts
from takahe.utils import Takahe
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _member(username: str) -> tuple[User, Client]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return user, client


def _item_notes(client: Client, book: Edition) -> str:
    url = reverse("catalog:notes", args=[book.url_path, book.uuid])
    return client.get(url).content.decode()


def test_item_note_reply_opens_replies_inline():
    user, client = _member("notereply")
    book = Edition.objects.create(title="Reply Book")
    note = Note.objects.create(
        item=book, owner=user.identity, content="my own note", visibility=0
    )
    assert note.latest_post_id

    content = _item_notes(client, book)

    # notes have no page of their own, so a link to one is a 404
    assert f'href="{note.url}"' not in content
    assert reverse("journal:piece_replies", args=[note.uuid]) in content
    assert f'id="replies_{note.latest_post_id}"' in content


def test_item_note_renders_inline_spoiler():
    user, client = _member("notespoiler")
    book = Edition.objects.create(title="Spoiler Book")
    Note.objects.create(
        item=book,
        owner=user.identity,
        content="before >!the twist!< after\n<b>x</b>",
        visibility=0,
    )

    content = _item_notes(client, book)

    assert '<span class="spoiler"' in content
    assert "the twist</span>" in content
    assert "&gt;!" not in content
    assert "&lt;b&gt;x&lt;/b&gt;" in content


def test_note_spoiler_makes_post_sensitive_without_markup():
    user = User.register(email="notepost@example.com", username="notepost")
    book = Edition.objects.create(title="Post Book")
    note = Note.objects.create(
        item=book,
        owner=user.identity,
        content="before >!the twist!< after",
        visibility=0,
    )
    assert note.latest_post_id
    post = Takahe.get_post(note.latest_post_id)
    assert post
    assert post.sensitive is True
    assert post.summary == spoiler_warning(book)
    assert ">!" not in post.content and "!&lt;" not in post.content
    assert "the twist" in post.content
    note.refresh_from_db()
    assert note.content == "before >!the twist!< after"
    assert note.sensitive is False

    params = note.to_crosspost_params()
    assert params["sensitive"] is True
    assert params["spoiler_text"] == spoiler_warning(book)
    assert ">!" not in params["content"]


def test_note_spoiler_keeps_title_as_summary():
    user = User.register(email="notetitle@example.com", username="notetitle")
    book = Edition.objects.create(title="Title Book")
    note = Note.objects.create(
        item=book,
        owner=user.identity,
        title="Chapter 3",
        content=">!the twist!<",
        visibility=0,
    )
    assert note.latest_post_id
    post = Takahe.get_post(note.latest_post_id)
    assert post
    assert post.summary == "Chapter 3"
    assert post.sensitive is True


def test_note_without_spoiler_post_unchanged():
    user = User.register(email="noteplain@example.com", username="noteplain")
    book = Edition.objects.create(title="Plain Book")
    note = Note(item=book, owner=user.identity, content="a > b and c ! d")
    params = note.to_post_params()
    assert params["summary"] is None
    assert params["content"] == "a > b and c ! d"
    assert params["sensitive"] is False


def test_local_edit_does_not_turn_spoiler_warning_into_title():
    user = User.register(email="noteedit@example.com", username="noteedit")
    book = Edition.objects.create(title="Edit Book")
    note = Note.objects.create(
        item=book, owner=user.identity, content=">!x!<", visibility=0
    )
    post = MagicMock()
    post.local = True
    post.summary = spoiler_warning(book)
    post.sensitive = True
    obj = {"type": "Note", "content": "x edited"}

    assert Note.params_from_ap_object(post, obj, note)["title"] is None

    post.summary = "a warning the member wrote"
    assert (
        Note.params_from_ap_object(post, obj, note)["title"]
        == "a warning the member wrote"
    )


def test_note_content_parts_splits_item_footer():
    user = User.register(email="notefooter@example.com", username="notefooter")
    book = Edition.objects.create(title="Footer Book")
    note = Note.objects.create(
        item=book,
        owner=user.identity,
        content="line one\n\n— not the footer\nline two",
        progress_type=Note.ProgressType.PAGE,
        progress_value="12",
        visibility=0,
    )
    assert note.latest_post_id
    post = Takahe.get_post(note.latest_post_id)
    assert post

    body, footer = note_content_parts(post)

    assert body + footer == post.safe_content_local
    assert "line two" in body
    assert "Footer Book" not in body
    assert footer.startswith("<p>—<br")
    assert "Footer Book</a>" in footer
    assert note.progress_display in footer


def test_note_content_parts_without_footer():
    post = MagicMock()
    post.safe_content_local = "<p>reply text</p>"
    assert note_content_parts(post) == ("<p>reply text</p>", "")


def test_profile_recent_posts_keep_note_footer_outside_clamp():
    user, client = _member("noteprofile")
    book = Edition.objects.create(title="Profile Book")
    note = Note.objects.create(
        item=book, owner=user.identity, content="long excerpt", visibility=0
    )
    assert note.latest_post_id

    content = client.get(user.identity.url).content.decode()

    start = content.index(f'id="content_{note.latest_post_id}"')
    block = content[start : content.index(f'id="quote_{note.latest_post_id}"')]
    assert 'class="content tldr"' not in content[start - 100 : start + 100]
    body_start = block.index('<div class="tldr"')
    body_end = block.index("</div>", body_start)
    assert "long excerpt" in block[body_start:body_end]
    assert "Profile Book</a>" in block[body_end : block.index("</blockquote>")]
