from collections.abc import Callable

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from activities.models import Post, PostInteraction, PreviewCard, TimelineEvent
from api.models import Application
from core.models import Config
from users.models import Identity


def _add_posts(
    identity: Identity,
    identity_factory: Callable[..., Identity],
    application: Application,
    start: int,
    count: int,
) -> list[Identity]:
    authors = []
    for n in range(start, start + count):
        author = identity_factory(username=f"author{n}")
        card = PreviewCard.objects.create(
            url=f"https://example.com/card/{n}", state="fetched"
        )
        post = Post.create_local(author=author, content=f"<p>post {n}</p>")
        Post.objects.filter(pk=post.pk).update(
            preview_card=card, application=application
        )
        TimelineEvent.add_post(identity, post)
        authors.append(author)
    booster = identity_factory(username=f"booster{start}")
    boost = PostInteraction.objects.create(
        type=PostInteraction.Types.boost, identity=booster, post=post
    )
    TimelineEvent.add_post_interaction(identity, boost)
    return authors


def _home_queries(api_client) -> tuple[int, list[dict]]:
    with CaptureQueriesContext(connection) as ctx:
        response = api_client.get("/api/v1/timelines/home?limit=40")
    assert response.status_code == 200
    return len(ctx.captured_queries), response.json()


@pytest.mark.django_db
def test_home_timeline_query_count_constant(api_client, identity, identity_factory):
    application = Application.objects.create(
        client_id="cid", client_secret="secret", name="Test App"
    )
    authors = _add_posts(identity, identity_factory, application, 0, 2)
    Config.set_identity(authors[0], "visible_follows", False)
    _home_queries(api_client)  # warm the system config cache
    small_count, small = _home_queries(api_client)

    _add_posts(identity, identity_factory, application, 2, 4)
    large_count, large = _home_queries(api_client)

    assert len(small) == 3
    assert len(large) == 8
    assert large_count == small_count
    statuses = [s for s in large if s["reblog"] is None]
    assert all(
        s["card"]["url"].startswith("https://example.com/card/") for s in statuses
    )
    assert all(s["application"]["name"] == "Test App" for s in statuses)
    hidden = {
        s["account"]["username"] for s in large if s["account"]["hide_collections"]
    }
    assert hidden == {"author0"}
