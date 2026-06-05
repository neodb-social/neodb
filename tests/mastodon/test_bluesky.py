from types import SimpleNamespace

from mastodon.models.bluesky import BlueskyAccount


def _page(attr, dids, cursor):
    return SimpleNamespace(
        cursor=cursor, **{attr: [SimpleNamespace(did=d) for d in dids]}
    )


def test_paginate_dids_walks_cursor():
    account = BlueskyAccount()
    pages = [
        _page("follows", ["did:a", "did:b"], "c1"),
        _page("follows", ["did:c"], None),
    ]
    seen: list[str | None] = []

    def fetch(cursor):
        seen.append(cursor)
        return pages.pop(0)

    dids = account._paginate_dids(fetch, "follows")

    assert dids == ["did:a", "did:b", "did:c"]
    assert seen == [None, "c1"]  # second call carries the first page's cursor


def test_paginate_dids_bounded_by_max_pages():
    account = BlueskyAccount()

    def fetch(cursor):  # never-ending cursor
        return _page("mutes", ["did:x"], "more")

    dids = account._paginate_dids(fetch, "mutes", max_pages=3)

    assert dids == ["did:x", "did:x", "did:x"]
