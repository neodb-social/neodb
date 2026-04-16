import pytest

from catalog.models import Edition
from journal.models import Tag, TagManager, TagMember
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestTagUpdate:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tag@test.com", username="taguser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="Tag Book")

    def test_update_title(self):
        tag = Tag.objects.create(owner=self.identity, title="old_title", visibility=0)
        tag.update(title="new_title")
        tag.refresh_from_db()
        assert tag.title == "new_title"

    def test_update_visibility(self):
        tag = Tag.objects.create(owner=self.identity, title="vis_tag", visibility=0)
        tag.update(title="vis_tag", visibility=2)
        tag.refresh_from_db()
        assert tag.visibility == 2

    def test_update_visibility_to_public(self):
        tag = Tag.objects.create(owner=self.identity, title="vis_tag", visibility=2)
        tag.update(title="vis_tag", visibility=0)
        tag.refresh_from_db()
        assert tag.visibility == 0

    def test_update_pinned(self):
        tag = Tag.objects.create(owner=self.identity, title="pin_tag", visibility=0)
        tag.update(title="pin_tag", pinned=True)
        tag.refresh_from_db()
        assert tag.pinned is True

    def test_tag_to_indexable_doc(self):
        tag = Tag.objects.create(owner=self.identity, title="idx", visibility=0)
        assert tag.to_indexable_doc() == {}


@pytest.mark.django_db(databases="__all__")
class TestTagMemberProperties:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tm@test.com", username="tmuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="TM Book")

    def test_tagmember_title(self):
        tag = Tag.objects.create(owner=self.identity, title="MyTag", visibility=0)
        tag.append_item(self.book)
        member = TagMember.objects.filter(parent=tag, item=self.book).first()
        assert member is not None
        assert member.title == "MyTag"

    def test_tagmember_to_indexable_doc(self):
        tag = Tag.objects.create(owner=self.identity, title="MyTag2", visibility=0)
        tag.append_item(self.book)
        member = TagMember.objects.filter(parent=tag, item=self.book).first()
        assert member is not None
        assert member.to_indexable_doc() == {}


@pytest.mark.django_db(databases="__all__")
class TestTagAttachToItems:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user1 = User.register(email="ati1@test.com", username="atiuser1")
        self.user2 = User.register(email="ati2@test.com", username="atiuser2")
        self.book1 = Edition.objects.create(title="ATI Book1")
        self.book2 = Edition.objects.create(title="ATI Book2")

    def test_attach_tags_to_items(self):
        TagManager.tag_item_for_owner(
            self.user1.identity, self.book1, ["sci-fi", "space"]
        )
        TagManager.tag_item_for_owner(
            self.user2.identity, self.book1, ["sci-fi", "adventure"]
        )
        items = [self.book1, self.book2]
        Tag.attach_to_items(items)
        # book1 should have tags, book2 should have empty list
        assert hasattr(self.book1, "tags")
        assert len(self.book1.tags) > 0
        assert hasattr(self.book2, "tags")
        assert self.book2.tags == []

    def test_attach_tags_empty_items(self):
        result = Tag.attach_to_items([])
        assert result == []


@pytest.mark.django_db(databases="__all__")
class TestTagManagerGetItemsTags:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="git@test.com", username="gituser")
        self.identity = self.user.identity
        self.book1 = Edition.objects.create(title="GIT Book1")
        self.book2 = Edition.objects.create(title="GIT Book2")
        self.book3 = Edition.objects.create(title="GIT Book3")

    def test_get_items_tags_with_tags(self):
        TagManager.tag_item_for_owner(self.identity, self.book1, ["a", "b"])
        TagManager.tag_item_for_owner(self.identity, self.book2, ["c"])
        tm = TagManager(self.identity)
        result = tm.get_items_tags([self.book1.pk, self.book2.pk, self.book3.pk])
        assert sorted(result[self.book1.pk]) == ["a", "b"]
        assert result[self.book2.pk] == ["c"]
        assert result[self.book3.pk] == []

    def test_get_items_tags_empty_list(self):
        tm = TagManager(self.identity)
        result = tm.get_items_tags([])
        assert result == {}
