from datetime import timedelta

import pytest
from django.core.cache import cache

from catalog.jobs.discover import DiscoverGenerator, PopularPostsGenerator
from common.models import SiteConfig


class TestDiscoverIntervals:
    def test_posts_interval_uses_posts_knob(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "discover_posts_update_interval", 15)
        monkeypatch.setattr(SiteConfig.system, "discover_update_interval", 60)
        assert PopularPostsGenerator.get_interval() == timedelta(minutes=15)

    def test_gallery_interval_uses_gallery_knob(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "discover_gallery_update_interval", 360)
        monkeypatch.setattr(SiteConfig.system, "discover_update_interval", 60)
        assert DiscoverGenerator.get_interval() == timedelta(minutes=360)

    def test_zero_falls_back_to_deprecated_interval(self, monkeypatch):
        # 0 means "inherit the deprecated discover_update_interval".
        monkeypatch.setattr(SiteConfig.system, "discover_update_interval", 90)
        monkeypatch.setattr(SiteConfig.system, "discover_posts_update_interval", 0)
        monkeypatch.setattr(SiteConfig.system, "discover_gallery_update_interval", 0)
        assert PopularPostsGenerator.get_interval() == timedelta(minutes=90)
        assert DiscoverGenerator.get_interval() == timedelta(minutes=90)


@pytest.mark.django_db(databases="__all__")
class TestDiscoverCacheKeySeparation:
    """The two jobs own disjoint cache keys so neither stomps the other."""

    def test_popular_posts_job_only_writes_post_keys(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "discover_show_popular_posts", True)
        cache.delete_many(["popular_posts", "trends_statuses", "public_gallery"])

        PopularPostsGenerator().run()

        # post feeds are (re)written, even if empty on a fresh DB
        assert cache.get("popular_posts") == []
        assert cache.get("trends_statuses") == []
        # gallery keys are left untouched by the posts job
        assert cache.get("public_gallery") is None

    def test_gallery_job_does_not_write_post_keys(self, monkeypatch):
        monkeypatch.setattr(SiteConfig.system, "discover_show_popular_posts", True)
        cache.delete_many(
            ["popular_posts", "trends_statuses", "public_gallery", "trends_updated"]
        )

        DiscoverGenerator().run()

        # gallery keys are written
        assert cache.get("public_gallery") is not None
        assert cache.get("trends_updated") is not None
        # the gallery job must not touch the popular-posts feeds
        assert cache.get("popular_posts") is None
        assert cache.get("trends_statuses") is None
