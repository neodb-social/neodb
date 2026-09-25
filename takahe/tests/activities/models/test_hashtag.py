import pytest
from django.utils import timezone

from activities.models import Hashtag, HashtagStates, Post


def _states() -> dict[str, str]:
    return dict(Hashtag.objects.values_list("hashtag", "state"))


@pytest.mark.django_db
def test_ensure_hashtags(django_assert_num_queries):
    Hashtag.objects.create(
        hashtag="old", state=HashtagStates.updated, stats_updated=timezone.now()
    )

    # existing tags, insert new tags, update states
    with django_assert_num_queries(3):
        Hashtag.ensure_hashtags(["#Old", "new1", "NEW1", "new2"], update=True)
    assert _states() == {
        "old": HashtagStates.outdated,
        "new1": HashtagStates.outdated,
        "new2": HashtagStates.outdated,
    }

    Hashtag.objects.filter(hashtag="old").update(state=HashtagStates.updated)
    Hashtag.ensure_hashtags(["old", "new3"])
    states = _states()
    assert states["old"] == HashtagStates.updated
    assert states["new3"] == HashtagStates.outdated


@pytest.mark.django_db
def test_post_ensure_hashtags_query_count(django_assert_num_queries):
    Hashtag.objects.create(hashtag="a")
    post = Post(hashtags=["a", "b", "c", "d", "e"])
    with django_assert_num_queries(3):
        post.ensure_hashtags()
    assert set(Hashtag.objects.values_list("hashtag", flat=True)) == set("abcde")
