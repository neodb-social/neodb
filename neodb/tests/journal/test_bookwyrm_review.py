from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from catalog.common.downloaders import set_mock_mode
from catalog.models import Edition, ExternalResource, IdType
from journal.models import Article, Rating, Review
from journal.models.common import prefetch_pieces_for_posts
from takahe.ap_handlers import (
    bookwyrm_review_title,
    link_bookwyrm_review,
    post_deleted,
    post_fetched,
)
from takahe.models import Domain, Post
from takahe.models import Identity as TakaheIdentity
from takahe.utils import Takahe
from users.models import APIdentity

HOST = "bookwyrm.example"
BOOK = f"https://{HOST}/book/101"


@pytest.mark.parametrize(
    "name,rated,title",
    [
        (
            'Review of "The Geomagician" (4 stars): Come for the dinos. Stay for the…',
            True,
            "Come for the dinos. Stay for the…",
        ),
        ('Resenha de "The beauty" (5 estrelas): Unforgettable', True, "Unforgettable"),
        ('Review of "X" (4.5 stars): Great (really): yes', True, "Great (really): yes"),
        ('Review of "Book (2nd ed)" (3 stars): Fine', True, "Fine"),
        ('Review of "X": no rating here', False, "no rating here"),
        ("Critique de « Livre »: Bien", False, "Bien"),
        ('Review of "X" (4 stars): None', True, 'Review of "X" (4 stars)'),
        ('Review of "X": None', False, 'Review of "X"'),
        ("Something else entirely", False, "Something else entirely"),
    ],
)
def test_review_title(name, rated, title):
    assert bookwyrm_review_title(name, rated) == title


def _review(n: int = 1, **kw) -> dict:
    d = {
        "id": f"https://{HOST}/user/reader/review/{n}",
        "type": "Article",
        "name": 'Review of "The Geomagician" (4 stars): Come for the dinos',
        "content": "<p>Dinosaurs <em>and</em> magic.</p>",
        "published": "2026-10-01T12:00:00.000000+00:00",
        "attributedTo": f"https://{HOST}/user/reader",
        "inReplyToBook": BOOK,
        "rating": 4.0,
        "sensitive": False,
        "tag": [],
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    }
    d.update(kw)
    return d


@pytest.mark.django_db(databases="__all__")
class TestBookWyrmReview:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        for key in [
            f"dl:{BOOK}",
            f"dl:https://{HOST}/book/103",
            f"dl:https://{HOST}/author/410578",
        ]:
            cache.delete(key)
        domain, _ = Domain.objects.get_or_create(domain=HOST, defaults={"local": False})
        self.identity = TakaheIdentity.objects.create(
            actor_uri=f"https://{HOST}/user/reader",
            local=False,
            username="reader",
            domain=domain,
        )
        set_mock_mode(True)
        signed = MagicMock(status_code=404)
        with (
            patch("takahe.auth.sign_get", return_value=signed),
            patch("catalog.sites.bookwyrm.is_valid_url", return_value=True),
        ):
            yield
        set_mock_mode(False)

    def _post(self, obj: dict, type: str = "Article") -> Post:
        return Post.objects.create(
            author_id=self.identity.pk,
            local=False,
            object_uri=obj["id"],
            content=obj.get("content", ""),
            type=type,
            type_data={"object": obj},
            visibility=Post.Visibilities.public,
            state="fanned_out",
        )

    def _item(self) -> Edition:
        item = ExternalResource.objects.get(id_type=IdType.BookWyrm, id_value=BOOK).item
        assert isinstance(item, Edition)
        return item

    def test_rated_review(self):
        obj = _review()
        post_fetched(self._post(obj).pk, obj)
        owner = APIdentity.objects.get(pk=self.identity.pk)
        item = self._item()
        assert isinstance(item, Edition)
        review = Review.objects.get(owner=owner, item=item)
        assert review.title == "Come for the dinos"
        assert review.body.strip() == "Dinosaurs *and* magic."
        assert review.remote_id == obj["id"]
        assert not review.local
        rating = Rating.objects.get(owner=owner, item=item)
        assert rating.grade == 8
        assert rating.remote_id == obj["id"] + "#rating"
        assert not Article.objects.filter(owner=owner).exists()

    def test_existing_item_by_isbn(self):
        existing = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "The Geomagician"}]
        )
        existing.isbn = "9780593983317"
        existing.save()
        obj = _review(rating=2.5)
        post_fetched(self._post(obj).pk, obj)
        assert Review.objects.get(item=existing).title == "Come for the dinos"
        assert Rating.objects.get(item=existing).grade == 5

    def test_unrated_review(self):
        obj = _review(name='Review of "The Geomagician": Hmm', rating=None)
        post_fetched(self._post(obj).pk, obj)
        assert Review.objects.get(item=self._item()).title == "Hmm"
        assert not Rating.objects.filter(item=self._item()).exists()

    def test_spoiler_keeps_rating_only(self):
        obj = _review(sensitive=True, rating=5)
        post_fetched(self._post(obj).pk, obj)
        assert Rating.objects.get(item=self._item()).grade == 10
        assert not Review.objects.filter(item=self._item()).exists()

    def test_unrated_spoiler_links_nothing(self):
        obj = _review(sensitive=True, rating=None)
        post = self._post(obj)
        owner = Takahe.get_or_create_remote_apidentity(self.identity)
        assert link_bookwyrm_review(owner, post, obj) == "spoiler"
        assert not Review.objects.filter(owner=owner).exists()
        assert not Rating.objects.filter(owner=owner).exists()

    def test_edit_updates_and_drops_rating(self):
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        edited = _review(
            name='Review of "The Geomagician": Changed my mind',
            rating=None,
            # newer than the stored row, whose edited_time is auto_now
            updated=(timezone.now() + timedelta(minutes=1)).isoformat(),
        )
        post_fetched(post.pk, edited)
        assert Review.objects.get(item=self._item()).title == "Changed my mind"
        assert not Rating.objects.filter(item=self._item()).exists()

    def test_post_stands_for_review(self):
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        review = Review.objects.get(remote_id=obj["id"])
        assert Post.objects.get(pk=post.pk).piece == review
        posts = [Post.objects.get(pk=post.pk)]
        prefetch_pieces_for_posts(posts)
        assert posts[0].piece == review

    def test_edit_adding_spoiler_drops_review(self):
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        edited = _review(
            sensitive=True,
            updated=(timezone.now() + timedelta(minutes=1)).isoformat(),
        )
        post_fetched(post.pk, edited)
        assert not Review.objects.filter(remote_id=obj["id"]).exists()
        assert Rating.objects.get(remote_id=obj["id"] + "#rating").grade == 8
        assert Post.objects.filter(pk=post.pk).exists()

    def test_delayed_edits_apply_removals(self):
        # both versions predate their delivery here
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        post_fetched(
            post.pk,
            _review(rating=None, sensitive=True, updated="2026-10-02T12:00:00+00:00"),
        )
        assert not Rating.objects.filter(remote_id=obj["id"] + "#rating").exists()
        assert not Review.objects.filter(remote_id=obj["id"]).exists()

    def test_stale_version_keeps_rating(self):
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        # an older, unrated version delivered after the newer one
        post_fetched(post.pk, _review(rating=None))
        assert Rating.objects.get(remote_id=obj["id"] + "#rating").grade == 8

    def test_invalid_timestamp(self):
        obj = _review(published="yesterday")
        post = self._post(obj)
        owner = Takahe.get_or_create_remote_apidentity(self.identity)
        assert link_bookwyrm_review(owner, post, obj) == "invalid"

    def test_backfill_keeps_newest_review(self):
        older = _review(1, name='Review of "X" (2 stars): Old take', rating=2)
        newer = _review(2, name='Review of "X" (5 stars): New take', rating=5)
        p1 = self._post(older)
        p2 = self._post(newer)
        Post.objects.filter(pk=p1.pk).update(
            published=timezone.now() - timedelta(days=30)
        )
        Post.objects.filter(pk=p2.pk).update(
            published=timezone.now() - timedelta(days=1)
        )
        with patch(
            "catalog.sites.bookwyrm.BookWyrm.fetch_fedidb_hosts", return_value={}
        ):
            call_command(
                "journal",
                "bookwyrm-reviews",
                "--fix",
                "--delay",
                "0",
                stdout=StringIO(),
            )
        review = Review.objects.get(item=self._item())
        assert review.title == "New take"
        assert Rating.objects.get(item=self._item()).grade == 10

    def test_delete_removes_review_and_rating(self):
        obj = _review()
        post = self._post(obj)
        post_fetched(post.pk, obj)
        post_deleted(post.pk, False, obj)
        assert not Review.objects.filter(remote_id=obj["id"]).exists()
        assert not Rating.objects.filter(remote_id=obj["id"] + "#rating").exists()

    def test_unfetchable_book(self):
        obj = _review(inReplyToBook=f"https://{HOST}/book/105")
        post_fetched(self._post(obj).pk, obj)
        assert not Review.objects.filter(remote_id=obj["id"]).exists()

    def test_other_articles_left_alone(self):
        obj = _review()
        del obj["inReplyToBook"]
        post_fetched(self._post(obj).pk, obj)
        # no identity mirror is made for an Article NeoDB does not map
        assert not APIdentity.objects.filter(pk=self.identity.pk).exists()
        assert not Article.objects.exists()

    def test_note_with_book_link_left_alone(self):
        obj = _review(type="Note")
        post_fetched(self._post(obj, type="Note").pk, obj)
        assert not APIdentity.objects.filter(pk=self.identity.pk).exists()

    def test_backfill(self):
        done = _review(1)
        post = self._post(done)
        post_fetched(post.pk, done)
        pending = _review(2, rating=3, inReplyToBook=f"https://{HOST}/book/103")
        self._post(pending)
        cache.delete(Takahe.BOOKWYRM_PEERS_CACHE_KEY)
        fedidb = {"listed.example": "0.7.5"}
        with patch(
            "catalog.sites.bookwyrm.BookWyrm.fetch_fedidb_hosts", return_value=fedidb
        ):
            out = StringIO()
            call_command("journal", "bookwyrm-reviews", stdout=out)
            summary = out.getvalue().splitlines()[-1]
            assert "already_linked 1" in summary and "to_link 1" in summary
            assert "1 unknown would be registered" in out.getvalue()
            assert not Domain.objects.filter(pk="listed.example").exists()
            assert not Review.objects.filter(remote_id=pending["id"]).exists()

            out = StringIO()
            call_command(
                "journal", "bookwyrm-reviews", "--fix", "--delay", "0", stdout=out
            )
        summary = out.getvalue().splitlines()[-1]
        assert "already_linked 1" in summary and "linked 1" in summary
        assert (Domain.objects.get(pk="listed.example").nodeinfo or {})["software"] == {
            "name": "bookwyrm",
            "version": "0.7.5",
        }
        assert (
            Review.objects.get(remote_id=pending["id"]).item.display_title
            == "Small Press Zine No. 7"
        )
        assert Rating.objects.get(remote_id=pending["id"] + "#rating").grade == 6
        cache.delete(Takahe.BOOKWYRM_PEERS_CACHE_KEY)
