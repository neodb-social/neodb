import pytest

from catalog.models import Edition
from journal.models import Tag
from users.models import User


@pytest.mark.django_db(databases="__all__")
def test_attach_to_items_sets_public_tags():
    owner = User.register(email="tagger@example.com", username="tagger")
    other = User.register(email="tagger2@example.com", username="tagger2")
    tagged = Edition.objects.create(title="Tagged Book")
    untagged = Edition.objects.create(title="Untagged Book")

    tag = Tag.objects.create(owner=owner.identity, title="Sci-Fi", visibility=0)
    dup = Tag.objects.create(owner=other.identity, title="sci fi", visibility=0)
    hidden = Tag.objects.create(owner=other.identity, title="Hidden", visibility=1)
    tag.append_item(tagged)
    dup.append_item(tagged)
    hidden.append_item(tagged)

    Tag.attach_to_items([tagged, untagged])

    assert tagged.tags == ["sci fi"]
    assert untagged.tags == []
