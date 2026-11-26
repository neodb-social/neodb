from datetime import datetime, timezone

import pytest
from django.test import Client
from django.urls import reverse
from lxml import html

from catalog.models import (
    Album,
    Edition,
    Game,
    Item,
    ItemCredit,
    Movie,
    Performance,
    PerformanceProduction,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
    Work,
)
from journal.models import Mark, ShelfType
from takahe.utils import Takahe
from users.models import User

DETAIL_PAGE_MODELS: list[type[Item]] = [
    Album,
    Edition,
    Game,
    Movie,
    Performance,
    PerformanceProduction,
    Podcast,
    TVSeason,
    TVShow,
    Work,
]

# Edition lists publisher credits under the "publishing house" label already.
ROLES_NOT_ON_PAGE = {(Edition, "publishing_house")}


def _create_item(model: type[Item], **kwargs) -> Item:
    if model is TVSeason:
        kwargs["show"] = TVShow.objects.create(title="Parent Show")
    elif model is PerformanceProduction:
        kwargs["show"] = Performance.objects.create(title="Parent Performance")
    return model.objects.create(**kwargs)


def _item_page(client: Client, item: Item) -> html.HtmlElement:
    response = client.get(item.url, follow=True)
    assert response.status_code == 200
    return html.fromstring(response.content.decode())


@pytest.mark.django_db(databases="__all__")
def test_catalog_item_pages(client: Client) -> None:
    book = Edition.objects.create(title="Web Book")
    movie = Movie.objects.create(title="Web Movie")
    show = TVShow.objects.create(title="Web Show")
    season = TVSeason.objects.create(title="Web Season", show=show, season_number=1)
    episode = TVEpisode.objects.create(
        title="Web Episode", season=season, episode_number=1
    )
    album = Album.objects.create(title="Web Album", artist=["Artist"])
    game = Game.objects.create(title="Web Game")
    podcast = Podcast.objects.create(title="Web Podcast", host=["Host"])
    podcast_episode = PodcastEpisode.objects.create(
        title="Web Podcast Episode",
        program=podcast,
        pub_date=datetime.now(tz=timezone.utc),
    )
    performance = Performance.objects.create(title="Web Performance")
    production = PerformanceProduction.objects.create(
        title="Web Production", show=performance
    )

    items = [
        book,
        movie,
        show,
        season,
        episode,
        album,
        game,
        podcast,
        podcast_episode,
        performance,
        production,
    ]
    for item in items:
        response = client.get(item.url, follow=True)
        assert response.status_code == 200
        assert item.display_title in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_catalog_discover(client: Client) -> None:
    response = client.get("/discover/", follow=True)
    assert response.status_code == 200

    user = User.register(email="searcher@example.com", username="searcher")
    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    response = authed_client.get("/discover/", follow=True)
    assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
def test_catalog_search(client: Client) -> None:
    user = User.register(email="searcher@example.com", username="searcher")
    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    book = Edition.objects.create(
        localized_title=[{"lang": "en", "text": "Searchable Book"}]
    )
    movie = Movie.objects.create(
        localized_title=[{"lang": "en", "text": "Searchable movie"}]
    )

    response = client.get("/search?q=Searchable", follow=True)
    assert response.status_code == 200
    assert book.url in response.content.decode()
    assert movie.url in response.content.decode()

    response = authed_client.get("/search?c=book&q=Searchable", follow=True)
    assert response.status_code == 200
    assert book.url in response.content.decode()

    # not testing the actual external search, just that the page loads
    response = authed_client.get("/search/external?c=book", follow=True)
    assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
def test_embed_is_only_available_for_podcasts(client: Client) -> None:
    movie = Movie.objects.create(title="No Embed Movie")
    podcast = Podcast.objects.create(title="Embed Podcast", host=["Host"])

    assert client.get(f"{movie.url}/embed").status_code == 404
    response = client.get(f"{podcast.url}/embed")
    assert response.status_code == 200
    assert "Embed Podcast" in response.content.decode()


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize(
    "view_name", ["comments", "comments_by_episode", "reviews", "notes"]
)
@pytest.mark.parametrize(
    "last", ["2026-01-01%2000%3A00%3A02.000000%2B0800", "2026-02-30 00:00:00"]
)
def test_item_piece_lists_ignore_malformed_cursor(
    client: Client, view_name: str, last: str
) -> None:
    show = TVShow.objects.create(title="Cursor Show")
    url = reverse(f"catalog:{view_name}", args=[show.url_path, show.uuid])

    response = client.get(url, {"last": last})

    assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize("model", DETAIL_PAGE_MODELS, ids=lambda m: m.__name__)
def test_item_page_shows_every_credit_role(client: Client, model: type[Item]) -> None:
    item = _create_item(model, title=f"Credits {model.__name__}")
    roles = [
        role
        for role, _ in model.credit_role_choices()
        if (model, role) not in ROLES_NOT_ON_PAGE
    ]
    for order, role in enumerate(roles):
        ItemCredit.objects.create(
            item=item, role=role, name=f"Name of {role}", order=order
        )

    text = _item_page(client, item).get_element_by_id("item-metadata").text_content()

    assert [role for role in roles if f"Name of {role}" not in text] == []


@pytest.mark.django_db(databases="__all__")
@pytest.mark.parametrize("model", DETAIL_PAGE_MODELS, ids=lambda m: m.__name__)
def test_item_page_shows_other_titles_and_language(
    client: Client, model: type[Item]
) -> None:
    titles = {"Main Title", "Titre Autre"}
    item = _create_item(
        model,
        localized_title=[
            {"lang": "en", "text": "Main Title"},
            {"lang": "fr", "text": "Titre Autre"},
        ],
        language=["fr"],
    )

    page = _item_page(client, item)
    heading = page.get_element_by_id("item-title").xpath(".//h1")[0].text_content()
    (other_title,) = [t for t in titles if t not in heading]
    text = page.get_element_by_id("item-metadata").text_content()

    assert other_title in text
    assert "French" in text


def _rate(user: User, item: Item, grade: int, visibility: int = 0) -> None:
    Mark(user.identity, item).update(
        ShelfType.COMPLETE, rating_grade=grade, visibility=visibility
    )


def _users(prefix: str, *names: str) -> dict[str, User]:
    return {
        n: User.register(email=f"{prefix}-{n}@example.com", username=f"{prefix}{n}")
        for n in names
    }


def _following_raters(page: html.HtmlElement, item: Item) -> list[str]:
    url = reverse("catalog:mark_list", args=[item.url_path, item.uuid, "following"])
    return [
        a.text_content().strip()
        for a in page.xpath(f'//div[contains(@class, "rating")]//a[@href="{url}"]')
    ]


@pytest.mark.django_db(databases="__all__")
def test_item_page_counts_raters_among_people_you_follow(client: Client) -> None:
    u = _users("fr", "viewer", "pub", "fol", "priv", "stranger", "extra")
    book = Edition.objects.create(title="Followed Book")
    for n in ("pub", "fol", "priv"):
        Takahe.follow(u["viewer"].identity.pk, u[n].identity.pk, force_accept=True)
    _rate(u["pub"], book, 8)
    _rate(u["fol"], book, 6, visibility=1)
    _rate(u["priv"], book, 4, visibility=2)
    _rate(u["stranger"], book, 10)
    _rate(u["extra"], book, 7)
    _rate(u["viewer"], book, 9)

    assert _following_raters(_item_page(client, book), book) == []

    client.force_login(u["viewer"], backend="mastodon.auth.OAuth2Backend")
    page = _item_page(client, book)
    assert page.xpath('//div[contains(@class, "rating")]//h3')
    assert _following_raters(page, book) == ["2 from people you follow"]
    assert not page.xpath('//div[contains(@class, "undisplay")]//a')


@pytest.mark.django_db(databases="__all__")
def test_item_page_shows_followed_raters_without_an_average(client: Client) -> None:
    u = _users("fu", "viewer", "friend")
    book = Edition.objects.create(title="Rarely Rated")
    Takahe.follow(u["viewer"].identity.pk, u["friend"].identity.pk, force_accept=True)
    _rate(u["friend"], book, 8)

    client.force_login(u["viewer"], backend="mastodon.auth.OAuth2Backend")
    page = _item_page(client, book)
    assert page.xpath(
        '//div[contains(@class, "rating") and contains(@class, "unavailable")]'
    )
    links = page.xpath('//div[contains(@class, "undisplay")]//a')
    assert [a.text_content().strip() for a in links] == ["1 from people you follow"]


@pytest.mark.django_db(databases="__all__")
def test_item_page_counts_a_follow_once_across_seasons(client: Client) -> None:
    u = _users("fs", "viewer", "friend")
    show = TVShow.objects.create(title="Long Show")
    seasons = [
        TVSeason.objects.create(title=f"S{n}", show=show, season_number=n)
        for n in (1, 2)
    ]
    Takahe.follow(u["viewer"].identity.pk, u["friend"].identity.pk, force_accept=True)
    for s in seasons:
        _rate(u["friend"], s, 8)

    client.force_login(u["viewer"], backend="mastodon.auth.OAuth2Backend")
    assert _following_raters(_item_page(client, show), show) == [
        "1 from people you follow"
    ]


@pytest.mark.django_db(databases="__all__")
def test_item_page_has_no_followed_raters_line_without_follows(client: Client) -> None:
    u = _users("fn", "viewer", "stranger")
    book = Edition.objects.create(title="Lonely Book")
    _rate(u["stranger"], book, 8)
    client.force_login(u["viewer"], backend="mastodon.auth.OAuth2Backend")
    assert _following_raters(_item_page(client, book), book) == []
