from datetime import timedelta

import pytest
from django.utils import timezone

from activities.models import Post, PostInteraction
from core.ld import format_ld_date


def make_poll(author, *, mode="oneOf", expires=timedelta(hours=1)):
    return Post.create_local(
        author=author,
        content="<p>Choose a route</p>",
        question={
            "type": "Question",
            "mode": mode,
            "options": [
                {"name": "Route A", "type": "Note", "votes": 0},
                {"name": "Route B", "type": "Note", "votes": 0},
                {"name": "Route C", "type": "Note", "votes": 0},
            ],
            "voter_count": 0,
            "end_time": format_ld_date(timezone.now() + expires),
        },
    )


def post_path(post):
    return f"/@{post.author.handle}/posts/{post.pk}/"


@pytest.mark.django_db
def test_anonymous_question_page_links_to_login(config_system, identity, client):
    post = make_poll(identity)
    path = post_path(post)

    page = client.get(path)

    assert page.status_code == 200
    assert b"Sign in to vote" in page.content
    assert b'name="choices"' in page.content
    assert b"disabled" in page.content

    response = client.post(
        f"{path}vote/",
        {"identity": identity.pk, "choices": "0"},
    )

    assert response.status_code == 302
    assert response.url.startswith("/auth/login/")


@pytest.mark.django_db
def test_question_page_casts_single_choice_vote(
    config_system,
    identity,
    identity2,
    client_with_user,
):
    post = make_poll(identity2)
    path = post_path(post)

    page = client_with_user.get(path, {"identity": identity.pk})

    assert page.status_code == 200
    assert b"Vote as" in page.content
    assert b"<button" in page.content
    assert b"Vote" in page.content

    response = client_with_user.post(
        f"{path}vote/",
        {"identity": identity.pk, "choices": "1"},
        follow=True,
    )

    assert response.status_code == 200
    assert b"Your vote has been recorded." in response.content
    assert b"You voted in this poll." in response.content

    post.refresh_from_db()
    poll = post.type_data.to_mastodon_json(post, identity=identity)
    assert poll["voted"]
    assert poll["own_votes"] == [1]
    assert poll["votes_count"] == 1
    assert poll["voters_count"] == 1


@pytest.mark.django_db
def test_question_page_rejects_vote_from_poll_author(
    config_system,
    identity,
    client_with_user,
):
    post = make_poll(identity)
    path = post_path(post)

    page = client_with_user.get(path, {"identity": identity.pk})

    assert page.status_code == 200
    assert b"You can't vote in your own poll." in page.content

    response = client_with_user.post(
        f"{path}vote/",
        {"identity": identity.pk, "choices": "0"},
        follow=True,
    )

    assert response.status_code == 200
    assert b"You can't vote in your own poll" in response.content
    assert not PostInteraction.objects.filter(
        post=post,
        identity=identity,
        type=PostInteraction.Types.vote,
    ).exists()


@pytest.mark.django_db
def test_question_page_rejects_expired_poll(
    config_system,
    identity,
    identity2,
    client_with_user,
):
    post = make_poll(identity2, expires=timedelta(seconds=-1))
    path = post_path(post)

    page = client_with_user.get(path, {"identity": identity.pk})

    assert page.status_code == 200
    assert b"This poll has ended." in page.content

    response = client_with_user.post(
        f"{path}vote/",
        {"identity": identity.pk, "choices": "0"},
        follow=True,
    )

    assert response.status_code == 200
    assert b"The poll has already ended" in response.content
    assert not PostInteraction.objects.filter(
        post=post,
        identity=identity,
        type=PostInteraction.Types.vote,
    ).exists()


@pytest.mark.django_db
def test_question_page_allows_additive_multiple_choice_votes(
    config_system,
    identity,
    identity2,
    client_with_user,
):
    post = make_poll(identity2, mode="anyOf")
    path = post_path(post)
    vote_path = f"{path}vote/"

    first_vote = client_with_user.post(
        vote_path,
        {"identity": identity.pk, "choices": "0"},
    )
    assert first_vote.status_code == 302

    page = client_with_user.get(path)

    assert page.status_code == 200
    assert b"Add votes" in page.content
    assert b'class="selected"' in page.content
    assert b"checked" in page.content

    second_vote = client_with_user.post(
        vote_path,
        {"identity": identity.pk, "choices": "1"},
        follow=True,
    )

    assert second_vote.status_code == 200
    assert b"Your vote has been recorded." in second_vote.content

    post.refresh_from_db()
    poll = post.type_data.to_mastodon_json(post, identity=identity)
    assert poll["own_votes"] == [0, 1]
    assert poll["votes_count"] == 2
    assert poll["voters_count"] == 1
