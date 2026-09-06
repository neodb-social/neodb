"""Ingest a small, fixed set of well known items so a fresh install has a
catalog to look at.

Every link below was checked against the source API for a cover image and
usable metadata before it was stored here. Books come from Google Books,
albums from MusicBrainz release-groups, games from IGDB, and films and shows
from TMDB. Shows are capped at three seasons, because each season is fetched
as a separate related resource.

The site API keys for Google Books, IGDB and TMDB must be configured, or
those categories fail to scrape.
"""

import time
from typing import Any

import django_rq
from django.core.management.base import CommandParser
from loguru import logger
from rq import Worker

from catalog.common import SiteManager
from catalog.common.downloaders import DownloadError
from catalog.sites import *  # noqa: F403
from common.management.base import SiteCommand

# Queues the ingest fans out onto. get_resource_ready() puts related
# resources (seasons, people) on "crawl", and those jobs enqueue more of
# the same, so --wait polls until both drain and stay drained.
QUEUES = ["crawl", "fetch"]

BOOKS: list[tuple[str, str]] = [
    ("Dune", "https://books.google.com/books?id=e_9MDwAAQBAJ"),
    ("Nineteen Eighty-Four", "https://books.google.com/books?id=Wbd9RAAACAAJ"),
    ("To Kill a Mockingbird", "https://books.google.com/books?id=TutbxAEACAAJ"),
    ("Pride and Prejudice", "https://books.google.com/books?id=5GbdTc9OJ78C"),
    ("The Great Gatsby", "https://books.google.com/books?id=gnQJEAAAQBAJ"),
    ("One Hundred Years of Solitude", "https://books.google.com/books?id=AfB8EAAAQBAJ"),
    ("Beloved", "https://books.google.com/books?id=sfmp6gjZGP8C"),
    ("The Left Hand of Darkness", "https://books.google.com/books?id=zhERCgAAQBAJ"),
    ("Brave New World", "https://books.google.com/books?id=sRXtAQAAQBAJ"),
    ("The Hobbit", "https://books.google.com/books?id=U799AY3yfqcC"),
    ("Neuromancer", "https://books.google.com/books?id=Bd_UZAdvUDIC"),
    ("Things Fall Apart", "https://books.google.com/books?id=2plPEAAAQBAJ"),
    ("Never Let Me Go", "https://books.google.com/books?id=qLfZf7f5_pkC"),
    ("The Handmaid's Tale", "https://books.google.com/books?id=cRPKOzWlOfUC"),
    ("The Road", "https://books.google.com/books?id=hoTU7NliHCwC"),
    ("Norwegian Wood", "https://books.google.com/books?id=-eqNkdtERRMC"),
    ("The Three-Body Problem", "https://books.google.com/books?id=QxbFBAAAQBAJ"),
    ("Snow Crash", "https://books.google.com/books?id=mqpvVydYo-8C"),
    ("Slaughterhouse-Five", "https://books.google.com/books?id=pWyLDQAAQBAJ"),
    ("Invisible Man", "https://books.google.com/books?id=d_a3QgAACAAJ"),
]

ALBUMS: list[tuple[str, str]] = [
    (
        "Abbey Road",
        "https://musicbrainz.org/release-group/9162580e-5df4-32de-80cc-f45a8d8a9b1d",
    ),
    (
        "The Dark Side of the Moon",
        "https://musicbrainz.org/release-group/f5093c06-23e3-404f-aeaa-40f72885ee3a",
    ),
    (
        "OK Computer",
        "https://musicbrainz.org/release-group/b1392450-e666-3926-a536-22c65f834433",
    ),
    (
        "Kind of Blue",
        "https://musicbrainz.org/release-group/8e8a594f-2175-38c7-a871-abb68ec363e7",
    ),
    (
        "Nevermind",
        "https://musicbrainz.org/release-group/1b022e01-4da6-387b-8658-8678046e4cef",
    ),
    (
        "Thriller",
        "https://musicbrainz.org/release-group/f32fab67-77dd-3937-addc-9062e28e4c37",
    ),
    (
        "Rumours",
        "https://musicbrainz.org/release-group/416bb5e5-c7d1-3977-8fd7-7c9daf6c2be6",
    ),
    (
        "To Pimp a Butterfly",
        "https://musicbrainz.org/release-group/d9103c72-3807-4378-9ce7-b6f3e8fdd547",
    ),
    (
        "Highway 61 Revisited",
        "https://musicbrainz.org/release-group/fb48b1dc-412f-36aa-8820-1023c08c46c6",
    ),
    (
        "What's Going On",
        "https://musicbrainz.org/release-group/c1fa4d2c-ec62-37d5-b01d-6df7f8fd2c90",
    ),
    (
        "Back to Black",
        "https://musicbrainz.org/release-group/6eac2e57-ee50-36f8-b0c4-c4c847a2c098",
    ),
    (
        "Discovery",
        "https://musicbrainz.org/release-group/48117b90-a16e-34ca-a514-19c702df1158",
    ),
    (
        "Blue",
        "https://musicbrainz.org/release-group/42d725fb-a8b7-388c-8866-3b02789af326",
    ),
    (
        "The Rise and Fall of Ziggy Stardust and the Spiders From Mars",
        "https://musicbrainz.org/release-group/6c9ae3dd-32ad-472c-96be-69d0a3536261",
    ),
    (
        "The Velvet Underground & Nico",
        "https://musicbrainz.org/release-group/5cbd9d7b-597a-3c5e-bfd1-c2b364215560",
    ),
    (
        "My Beautiful Dark Twisted Fantasy",
        "https://musicbrainz.org/release-group/5d6e21e1-deb5-428e-bb42-c2a567f3619b",
    ),
    (
        "Songs in the Key of Life",
        "https://musicbrainz.org/release-group/ea88b09b-fd34-33cf-a3e5-25a3a2fb4c6f",
    ),
    (
        "Mezzanine",
        "https://musicbrainz.org/release-group/6f9f6899-c0d3-311d-ae87-a10ae6bc53a9",
    ),
    (
        "Homogenic",
        "https://musicbrainz.org/release-group/810272e0-aef1-3d85-b2d3-e512e87fc38c",
    ),
    (
        "Lemonade",
        "https://musicbrainz.org/release-group/c1f22e07-7bdf-4a4f-8b50-7747c1091ef6",
    ),
]

GAMES: list[tuple[str, str]] = [
    ("Portal 2", "https://www.igdb.com/games/portal-2"),
    (
        "The Legend of Zelda: Breath of the Wild",
        "https://www.igdb.com/games/the-legend-of-zelda-breath-of-the-wild",
    ),
    ("Half-Life 2", "https://www.igdb.com/games/half-life-2"),
    ("The Witcher 3: Wild Hunt", "https://www.igdb.com/games/the-witcher-3-wild-hunt"),
    ("Red Dead Redemption 2", "https://www.igdb.com/games/red-dead-redemption-2"),
    ("Disco Elysium", "https://www.igdb.com/games/disco-elysium"),
    ("Hollow Knight", "https://www.igdb.com/games/hollow-knight"),
    ("Celeste", "https://www.igdb.com/games/celeste"),
    ("Dark Souls", "https://www.igdb.com/games/dark-souls"),
    ("Super Mario Odyssey", "https://www.igdb.com/games/super-mario-odyssey"),
    ("Stardew Valley", "https://www.igdb.com/games/stardew-valley"),
    ("Journey", "https://www.igdb.com/games/journey"),
    ("Bloodborne", "https://www.igdb.com/games/bloodborne"),
    ("Undertale", "https://www.igdb.com/games/undertale"),
    ("Mass Effect 2", "https://www.igdb.com/games/mass-effect-2"),
    ("Return of the Obra Dinn", "https://www.igdb.com/games/return-of-the-obra-dinn"),
    ("Hades", "https://www.igdb.com/games/hades--1"),
    ("Outer Wilds", "https://www.igdb.com/games/outer-wilds"),
    ("Chrono Trigger", "https://www.igdb.com/games/chrono-trigger"),
    ("Elden Ring", "https://www.igdb.com/games/elden-ring"),
]

MOVIES: list[tuple[str, str]] = [
    ("Fight Club", "https://www.themoviedb.org/movie/550"),
    ("The Godfather", "https://www.themoviedb.org/movie/238"),
    ("Pulp Fiction", "https://www.themoviedb.org/movie/680"),
    ("The Dark Knight", "https://www.themoviedb.org/movie/155"),
    ("Forrest Gump", "https://www.themoviedb.org/movie/13"),
    ("Inception", "https://www.themoviedb.org/movie/27205"),
    ("The Matrix", "https://www.themoviedb.org/movie/603"),
    ("The Shawshank Redemption", "https://www.themoviedb.org/movie/278"),
    ("Spirited Away", "https://www.themoviedb.org/movie/129"),
    (
        "The Lord of the Rings: The Return of the King",
        "https://www.themoviedb.org/movie/122",
    ),
    ("Interstellar", "https://www.themoviedb.org/movie/157336"),
    ("Parasite", "https://www.themoviedb.org/movie/496243"),
    ("Your Name.", "https://www.themoviedb.org/movie/372058"),
    ("Star Wars", "https://www.themoviedb.org/movie/11"),
    ("Back to the Future", "https://www.themoviedb.org/movie/105"),
    ("12 Angry Men", "https://www.themoviedb.org/movie/389"),
    ("Schindler's List", "https://www.themoviedb.org/movie/424"),
    ("Toy Story", "https://www.themoviedb.org/movie/862"),
    ("Seven Samurai", "https://www.themoviedb.org/movie/346"),
    (
        "Everything Everywhere All at Once",
        "https://www.themoviedb.org/movie/545611",
    ),
]

# Season counts are noted because every season is fetched as its own
# related resource. Keep additions to three seasons or fewer.
TV_SHOWS: list[tuple[str, str]] = [
    ("Chernobyl (1 season)", "https://www.themoviedb.org/tv/87108"),
    ("Band of Brothers (1 season)", "https://www.themoviedb.org/tv/4613"),
    ("The Queen's Gambit (1 season)", "https://www.themoviedb.org/tv/87739"),
    ("Watchmen (1 season)", "https://www.themoviedb.org/tv/79788"),
    ("Firefly (1 season)", "https://www.themoviedb.org/tv/1437"),
    ("Cowboy Bebop (1 season)", "https://www.themoviedb.org/tv/30991"),
    ("Fleabag (2 seasons)", "https://www.themoviedb.org/tv/67070"),
    ("Arcane (2 seasons)", "https://www.themoviedb.org/tv/94605"),
    ("Severance (3 seasons)", "https://www.themoviedb.org/tv/95396"),
    ("Dark (3 seasons)", "https://www.themoviedb.org/tv/70523"),
]

SEED_ITEMS: dict[str, list[tuple[str, str]]] = {
    "book": BOOKS,
    "album": ALBUMS,
    "game": GAMES,
    "movie": MOVIES,
    "tv": TV_SHOWS,
}


class Command(SiteCommand):
    help = "Ingest a fixed set of well known books, albums, games, films and shows"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--type",
            action="append",
            choices=sorted(SEED_ITEMS.keys()),
            help="only ingest this category; repeat for several (default: all)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="re-scrape items that are already in the catalog",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="wait for the queued related-resource jobs to finish",
        )
        parser.add_argument(
            "--wait-timeout",
            type=int,
            default=1800,
            help="seconds to wait with --wait before giving up (default: 1800)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        types: list[str] = options["type"] or sorted(SEED_ITEMS.keys())
        force: bool = options["force"]
        ingested = 0
        no_cover: list[str] = []
        failed: list[str] = []
        for category in types:
            entries = SEED_ITEMS[category]
            self.stdout.write(
                self.style.MIGRATE_HEADING(f"{category} ({len(entries)})")
            )
            for name, url in entries:
                ok, covered = self._ingest(name, url, force)
                if not ok:
                    failed.append(f"{category}: {name}")
                elif not covered:
                    no_cover.append(f"{category}: {name}")
                    ingested += 1
                else:
                    ingested += 1
        self.stdout.write(f"Ingested {ingested} items.")
        if no_cover:
            self.stdout.write(self.style.WARNING(f"No cover for {len(no_cover)}:"))
            for entry in no_cover:
                self.stdout.write(f"  {entry}")
        if failed:
            self.stdout.write(self.style.ERROR(f"Failed for {len(failed)}:"))
            for entry in failed:
                self.stdout.write(f"  {entry}")
        if options["wait"]:
            self._wait(options["wait_timeout"])
        self.stdout.write(self.style.SUCCESS("Done."))

    def _ingest(self, name: str, url: str, force: bool) -> tuple[bool, bool]:
        """Scrape and save one seed URL, returning (succeeded, has cover)."""
        # These URLs are already canonical, so skip the per-URL HEAD that
        # redirect detection would otherwise do.
        site = SiteManager.get_site_by_url(url, detect_redirection=False)
        if site is None:
            self.stdout.write(self.style.ERROR(f"  no site for {url}"))
            return False, False
        try:
            resource = site.get_resource_ready(ignore_existing_content=force)
        except DownloadError as e:
            # Expected third-party failure, so warn and carry on with the rest.
            logger.warning(f"unable to fetch {url}", extra={"exception": e})
            self.stdout.write(self.style.ERROR(f"  {name}: download failed"))
            return False, False
        except Exception as e:
            logger.error(f"error fetching {url}", extra={"exception": e})
            self.stdout.write(self.style.ERROR(f"  {name}: {e}"))
            return False, False
        if resource is None or resource.item is None:
            self.stdout.write(self.style.ERROR(f"  {name}: no item"))
            return False, False
        item = resource.item
        covered = item.has_cover()
        flag = "" if covered else "  [no cover]"
        self.stdout.write(f"  {item.display_title}{flag}")
        return True, covered

    def _wait(self, timeout: int) -> None:
        """Block until the fan-out queues drain.

        Related-resource jobs enqueue further jobs, so an empty queue is only
        conclusive once nothing is running either, and only if it stays that
        way across two consecutive polls.
        """
        queues = [django_rq.get_queue(q) for q in QUEUES]
        idle_workers = [q.name for q in queues if Worker.count(queue=q) == 0]
        if idle_workers:
            self.stdout.write(
                self.style.WARNING(
                    f"No worker is listening on {', '.join(idle_workers)}; "
                    "queued jobs will not run until one starts."
                )
            )
        deadline = time.monotonic() + timeout
        quiet = 0
        while time.monotonic() < deadline:
            pending = sum(
                q.count
                + q.started_job_registry.count
                + q.deferred_job_registry.count
                + q.scheduled_job_registry.count
                for q in queues
            )
            if pending == 0:
                quiet += 1
                if quiet >= 2:
                    self.stdout.write("Queued jobs finished.")
                    return
            else:
                quiet = 0
                self.stdout.write(f"  waiting on {pending} queued jobs")
            time.sleep(5)
        self.stdout.write(
            self.style.WARNING(f"Still busy after {timeout}s; stopped waiting.")
        )
