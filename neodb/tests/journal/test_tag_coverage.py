import pytest

from catalog.models import Edition
from journal.models import Tag, TagMember
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


@pytest.mark.django_db(databases="__all__")
class TestTagMemberTitle:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tm@test.com", username="tmuser")
        self.identity = self.user.identity
        self.book = Edition.objects.create(title="TM Book")

    def test_tagmember_title_from_parent(self):
        tag = Tag.objects.create(owner=self.identity, title="MyTag", visibility=0)
        tag.append_item(self.book)
        member = TagMember.objects.filter(parent=tag, item=self.book).first()
        assert member is not None
        assert member.title == "MyTag"
