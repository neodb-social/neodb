import pytest

from catalog.common.migrations import fix_legacy_brief_20260926
from catalog.models import ExternalResource, IdType, Item, Movie, TVEpisode
from catalog.models.utils import legacy_text, normalize_legacy_text_metadata
from journal.models import Mark, ShelfType
from users.models import User

PLOT = "A new story detailing their lives begins."
MARKDOWN = {"plainText": PLOT, "__typename": "Markdown"}
MARKDOWN_REPR = str(MARKDOWN)


class TestLegacyText:
    def test_values(self):
        assert legacy_text(MARKDOWN) == PLOT
        assert legacy_text(MARKDOWN_REPR) == PLOT
        assert legacy_text({"text": "t"}) == "t"
        assert legacy_text("plain") == "plain"
        assert legacy_text("{'plainText' is not a literal") == (
            "{'plainText' is not a literal"
        )
        assert legacy_text(None) is None
        assert legacy_text({"__typename": "Markdown"}) is None
        assert legacy_text(3) is None

    def test_metadata(self):
        metadata = {
            "title": "T",
            "brief": MARKDOWN,
            "localized_title": [{"lang": "en", "text": "T"}],
            "localized_description": [
                {"lang": "en", "text": MARKDOWN},
                {"lang": "fr", "text": None},
                {"lang": "de"},
            ],
        }
        normalize_legacy_text_metadata(metadata)
        assert metadata == {
            "title": "T",
            "brief": PLOT,
            "localized_title": [{"lang": "en", "text": "T"}],
            "localized_description": [{"lang": "en", "text": PLOT}],
        }

    def test_metadata_drops_empty_brief(self):
        metadata = {"brief": {"__typename": "Markdown"}}
        normalize_legacy_text_metadata(metadata)
        assert metadata == {}

    def test_clean_metadata_is_untouched(self):
        labels = [{"lang": "en", "text": "x"}]
        metadata = {"brief": "b", "localized_description": labels}
        normalize_legacy_text_metadata(metadata)
        assert metadata == {"brief": "b", "localized_description": labels}
        assert metadata["localized_description"] is labels


def _legacy_episode_resource(imdb_id: str = "tt11592198") -> ExternalResource:
    return ExternalResource.objects.create(
        id_type=IdType.IMDB,
        id_value=imdb_id,
        url=f"https://www.imdb.com/title/{imdb_id}/",
        metadata={
            "title": "Ren Sheng Xia Yi Guan",
            "brief": MARKDOWN,
            "season_number": 5,
            "episode_number": 1,
            "preferred_model": "TVEpisode",
        },
    )


@pytest.mark.django_db(databases="__all__")
class TestCreateFromLegacyResource:
    def test_create_episode(self):
        res = _legacy_episode_resource()
        item = TVEpisode.create_from_external_resource(res)
        item.refresh_from_db()
        assert item.brief == PLOT
        assert item.ap_object["brief"] == PLOT

    def test_create_movie_with_legacy_labels(self):
        res = ExternalResource.objects.create(
            id_type=IdType.IMDB,
            id_value="tt0000001",
            url="https://www.imdb.com/title/tt0000001/",
            metadata={
                "localized_title": [{"lang": "en", "text": "M"}],
                "localized_description": [{"lang": "en", "text": MARKDOWN}],
            },
        )
        item = Movie.create_from_external_resource(res)
        assert item.localized_description == [{"lang": "en", "text": PLOT}]

    def test_failed_validation_leaves_no_item(self, monkeypatch):
        def broken(self):
            raise ValueError("schema")

        monkeypatch.setattr(TVEpisode, "ap_object", property(broken))
        res = _legacy_episode_resource()
        before = Item.objects.count()
        with pytest.raises(ValueError):
            TVEpisode.create_from_external_resource(res)
        assert Item.objects.count() == before


@pytest.mark.django_db(databases="__all__")
class TestFixLegacyBriefMigration:
    def _setup(self):
        pending = _legacy_episode_resource()
        linked = TVEpisode.objects.create(
            title="Linked", brief=MARKDOWN_REPR, episode_number=2
        )
        ExternalResource.objects.create(
            item=linked,
            id_type=IdType.IMDB,
            id_value="tt0000002",
            url="https://www.imdb.com/title/tt0000002/",
            metadata={"title": "Linked", "brief": PLOT},
        )
        orphan = TVEpisode.objects.create(
            title="Orphan", brief=MARKDOWN_REPR, episode_number=1
        )
        marked = TVEpisode.objects.create(
            title="Marked", brief=MARKDOWN_REPR, episode_number=3
        )
        user = User.register(email="legacy@example.com", username="legacy")
        Mark(user.identity, marked).update(ShelfType.COMPLETE)
        movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "M"}],
            localized_description=[
                {"lang": "en", "text": MARKDOWN},
                {"lang": "fr", "text": None},
            ],
        )
        clean = Movie.objects.create(
            brief="{not legacy}",
            localized_title=[{"lang": "en", "text": "C"}],
        )
        return pending, linked, orphan, marked, movie, clean

    def test_dry_run_changes_nothing(self):
        pending, linked, orphan, _, movie, _ = self._setup()
        fix_legacy_brief_20260926(dry_run=True, delete_orphans=True)
        pending.refresh_from_db()
        assert pending.metadata["brief"] == MARKDOWN
        assert Item.objects.get(pk=linked.pk).brief == MARKDOWN_REPR
        assert not Item.objects.get(pk=orphan.pk).is_deleted
        assert Movie.objects.get(pk=movie.pk).localized_description[0]["text"] == (
            MARKDOWN
        )

    def test_fix_without_delete(self):
        pending, linked, orphan, marked, movie, clean = self._setup()
        fix_legacy_brief_20260926()
        pending.refresh_from_db()
        assert pending.metadata["brief"] == PLOT
        for item in (linked, orphan, marked):
            fixed = Item.objects.get(pk=item.pk)
            assert fixed.brief == PLOT
            assert not fixed.is_deleted
        assert Movie.objects.get(pk=movie.pk).localized_description == [
            {"lang": "en", "text": PLOT}
        ]
        assert Movie.objects.get(pk=clean.pk).brief == "{not legacy}"
        TVEpisode.create_from_external_resource(pending)

    def test_delete_orphans(self):
        _, linked, orphan, marked, _, _ = self._setup()
        fix_legacy_brief_20260926(delete_orphans=True)
        assert Item.objects.get(pk=orphan.pk).is_deleted
        assert not Item.objects.get(pk=linked.pk).is_deleted
        assert not Item.objects.get(pk=marked.pk).is_deleted
        fix_legacy_brief_20260926(delete_orphans=True)
