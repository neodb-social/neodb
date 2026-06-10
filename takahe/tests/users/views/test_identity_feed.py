import pytest

from activities.models import Post
from users.views.identity import IdentityFeed


@pytest.mark.django_db
def test_item_description_appends_quote_link(config_system, identity):
    post = Post.create_local(author=identity, content="Look here")
    post.quote_url = "https://remote.test/posts/abc"
    snippet = '<p>Quote: <a href="https://remote.test/posts/abc">https://remote.test/posts/abc</a></p>'
    assert snippet in IdentityFeed().item_description(post)


@pytest.mark.django_db
def test_item_description_escapes_quote_url(config_system, identity):
    post = Post.create_local(author=identity, content="Look here")
    post.quote_url = 'https://remote.test/posts/"><script>'
    description = IdentityFeed().item_description(post)
    assert 'href="https://remote.test/posts/&quot;&gt;&lt;script&gt;"' in description
    assert "<script>" not in description


@pytest.mark.django_db
def test_item_description_without_quote(config_system, identity):
    post = Post.create_local(author=identity, content="Just a post")
    assert "Quote:" not in IdentityFeed().item_description(post)
