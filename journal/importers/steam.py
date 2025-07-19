from datetime import datetime, timedelta
from typing import Iterable, List, Optional, TypedDict

import pytz
import requests
from django.conf import settings
from django.utils import timezone
from loguru import logger
from requests import HTTPError

from catalog.common.downloaders import DownloadError
from catalog.common.models import IdType, Item
from catalog.common.sites import SiteManager
from journal.models.common import VisibilityType
from journal.models.mark import Mark
from journal.models.shelf import ShelfType

from .base import BaseImporter

# with reference to
# - https://developer.valvesoftware.com/wiki/Steam_Web_API
# - https://steamapi.xpaw.me/
#
# Get played (owned) games from IPlayerService.GetOwnedGames
# Get wishlist games from IWishlistService/GetWishlist
# TODO: asynchronous item loading

STEAM_API_BASE_URL = "https://api.steampowered.com"


class RawGameMark(TypedDict):
    app_id: str
    shelf_type: ShelfType
    created_time: datetime
    raw_entry: dict


class InvalidSteamAPIKeyException(Exception):
    """
    Exception raised when the provided Steam API key is invalid.
    """


class InvalidSteamIDException(Exception):
    """
    Exception raised when the provided Steam ID is invalid.
    """


class SteamImporter(BaseImporter):
    class Meta:
        app_label = "journal"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    class MetaData(TypedDict):
        total: int
        skipped: int
        processed: int
        failed: int
        imported: int
        failed_items: List[str]
        visibility: VisibilityType
        steam_apikey: str
        steam_id: str
        config: dict

    TaskQueue = "import"
    DefaultMetadata: MetaData = {
        "total": 0,
        "skipped": 0,
        "processed": 0,
        "failed": 0,
        "imported": 0,
        "failed_items": [],
        "visibility": VisibilityType.Public,
        "steam_apikey": settings.STEAM_API_KEY or "",
        "steam_id": "",
        "config": {
            "wishlist": {
                "enable": False,
                "file": None,
            },
            "library": {
                "enable": False,
                "file": None,
                "include_played_free_games": False,
                "include_free_sub": False,
                "playing_thresh": 0,  # in hours
                "finish_thresh": 0,  # in days
                "last_play_to_ctime": True,  # use last played time as created time
            },
            "allow_shelf_type_reversion": False,
            "shelf_type_whitelist": [],
            "appid_blacklist": [],
        },
    }
    metadata: MetaData

    def failfast(self, message: str) -> None:
        logger.error(message)
        self.message += message
        self.state = self.States.failed
        self.save()

    def run(self):
        """
        Run task: fetch wishlist and/or owned games and import marks
        """
        logger.debug("Start importing")

        # Validation of apikey and userid
        try:
            self.validate(self.metadata["steam_apikey"], self.metadata["steam_id"])
        except InvalidSteamAPIKeyException:
            self.failfast(
                "Ask the site admin to set a valid STEAM_API_KEY to allow import from Steam"
            )
            return
        except InvalidSteamIDException as e:
            self.failfast(str(e))
            return
        except Exception:
            self.failfast("Unknown exception when validating api key / user id")
            return
        logger.debug("Steam API key and ID validated successfully")

        # Fetch and parse
        raw_marks: List[RawGameMark] = []
        try:
            if self.metadata["config"]["wishlist"]["enable"]:
                raw_marks.extend(
                    self.fetch_wishlist(**self.metadata["config"]["wishlist"])
                )
            if self.metadata["config"]["library"]["enable"]:
                raw_marks.extend(
                    self.fetch_library(**self.metadata["config"]["library"])
                )
        except HTTPError as e:
            self.failfast(f"HTTP error when fetching data: {e}")
            return
        logger.debug(f"{len(raw_marks)} raw marks fetched")

        # Filter
        try:
            raw_marks = list(
                filter(
                    lambda raw_mark: raw_mark["shelf_type"]
                    in self.metadata["config"]["shelf_type_whitelist"],
                    raw_marks,
                )
            )
            raw_marks = list(
                filter(
                    lambda raw_mark: raw_mark["app_id"]
                    not in self.metadata["config"]["appid_blacklist"],
                    raw_marks,
                )
            )
        except Exception as e:
            self.failfast(f"Exception when filtering: {e}")
            return

        # Update stat & messages
        self.metadata["total"] = len(raw_marks)
        logger.debug(f"{self.metadata['total']} raw marks after filter")

        # Start importing
        try:
            self.import_marks(raw_marks)
        except Exception as e:
            self.failfast(f"Unrecoverable exception when importing marks: {e}")
            self.metadata["failed"] += (
                self.metadata["total"] - self.metadata["processed"]
            )
            return

    def import_marks(self, raw_marks: Iterable[RawGameMark]):
        """
        Try import a list of RawGameMark as mark, scrape corresponding games if unavailable

        :param raw_marks: marks to import
        """

        logger.debug("Start importing marks")
        for raw_mark in raw_marks:
            item = self.get_item_by_id(raw_mark["app_id"])
            if item is None:
                logger.error(f"Failed to get item for {raw_mark}")
                self.progress("failed")
                self.metadata["failed_items"].append(raw_mark["raw_entry"]["appid"])
                continue
            logger.debug(f"Item fetched: {item}")

            mark = Mark(self.user.identity, item)
            logger.debug(f"Mark fetched: {mark}")

            if (
                not self.metadata["config"]["allow_shelf_type_reversion"]
                # if reversion is not allowed, then skip marked entry with reversion
                and (
                    mark.shelf_type == ShelfType.COMPLETE
                    or (
                        mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
                        and raw_mark["shelf_type"] == ShelfType.WISHLIST
                    )
                )
            ):
                logger.info(f"Game {mark.item.title} is already marked, skipping.")
                self.progress("skipped")
            else:
                mark.update(
                    shelf_type=raw_mark["shelf_type"],
                    visibility=self.metadata["visibility"],
                    created_time=raw_mark["created_time"].replace(
                        tzinfo=pytz.timezone(
                            timezone.get_current_timezone_name()
                        )  # use estimated tz from django
                    ),
                )
                logger.debug(f"Mark updated: {mark}")
                self.progress("imported")

    # Not using get_item_by_id for less overhead
    def get_item_by_id(
        self, app_id: str, id_type: IdType = IdType.Steam
    ) -> Item | None:
        site = SiteManager.get_site_by_id(id_type, app_id)
        if not site:
            raise ValueError(f"{id_type} not in site registry")
        item = site.get_item()
        if item:
            return item

        logger.debug(f"Scraping steam game {app_id}")
        try:
            site.get_resource_ready()
            item = site.get_item()
        except DownloadError as e:
            logger.error(f"Failed to fetch {e.url}")
            item = None
        except Exception as e:
            logger.error(f"Unexcepted error when getting item from appid {app_id}")
            logger.exception(e)
            item = None
        return item

    def fetch_library(
        self,
        file: Optional[dict] = None,
        include_played_free_games: bool = False,
        include_free_sub: bool = False,
        playing_thresh: int = 0,  # in days,
        finish_thresh: int = 0,  # in hours
        last_play_to_ctime: bool = True,  # use last played time as created time
        **kwargs: dict,
    ) -> List[RawGameMark]:
        if file is None:
            url = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v1/"
            params = {
                "key": self.metadata["steam_apikey"],
                "steamid": self.metadata["steam_id"],
                "include_appinfo": False,
                "include_played_free_games": include_played_free_games,
                "appids_filter": [],
                "include_free_sub": include_free_sub,
                "language": "en",  # appinfo not used, can be anything
                "include_extended_appinfo": False,
            }
            res = requests.get(url, params, timeout=1)
            res.raise_for_status()
            file = res.json()

        results = []
        for entry in file["response"]["games"]:  # type: ignore
            rtime_last_played = datetime.fromtimestamp(entry["rtime_last_played"])
            playtime_forever = entry["playtime_forever"]
            app_id = str(entry["appid"])
            shelf_type = self.estimate_shelf_type(
                playtime_forever,
                rtime_last_played,
                app_id,
                playing_thresh,
                finish_thresh,
            )
            created_time = (
                timezone.now()  # use current time as created_time for games purchased but never played, rtime is 0
                if (not last_play_to_ctime)
                or rtime_last_played == datetime.fromtimestamp(0)
                else rtime_last_played
            )
            results.append(
                RawGameMark(
                    app_id=app_id,
                    shelf_type=shelf_type,
                    created_time=created_time,
                    raw_entry=entry,
                )
            )

        return results

    def fetch_wishlist(self, file: Optional[dict], **kwargs) -> List[RawGameMark]:
        if file is None:
            url = f"{STEAM_API_BASE_URL}/IWishlistService/GetWishlist/v1/"
            params = {
                "key": self.metadata["steam_apikey"],
                "steamid": self.metadata["steam_id"],
            }
            res = requests.get(url, params, timeout=1)
            res.raise_for_status()
            file = res.json()

        results: List[RawGameMark] = []
        for entry in file["response"]["items"]:  # type: ignore
            created_time = datetime.fromtimestamp(entry["date_added"])
            results.append(
                RawGameMark(
                    app_id=str(entry["appid"]),
                    shelf_type=ShelfType.WISHLIST,
                    created_time=created_time,
                    raw_entry=entry,
                )
            )

        return results

    @classmethod
    def estimate_shelf_type(
        cls,
        playtime_forever: int,
        last_played: datetime,
        app_id: str,
        playing_thresh: int,
        finish_thresh: int,
    ):
        played_long_enough = playtime_forever > finish_thresh * 60
        never_played = playtime_forever == 0 and last_played == datetime.fromtimestamp(
            0
        )
        playing = datetime.now() - last_played < timedelta(days=playing_thresh)

        if never_played:
            return ShelfType.WISHLIST
        elif playing:
            return ShelfType.PROGRESS
        elif played_long_enough:
            return ShelfType.COMPLETE
        else:
            return ShelfType.DROPPED

    @classmethod
    def validate(cls, steam_apikey: str, steam_id: str) -> None:
        url = f"{STEAM_API_BASE_URL}/IPlayerService/GetSteamLevel/v1/"
        if not steam_apikey:
            logger.error(
                "Configure STEAM_API_KEY in environment to allow steam importer"
            )
            raise InvalidSteamAPIKeyException("Missing steam API key")
        params = {
            "key": steam_apikey,
            "steamid": steam_id,
        }
        resp = requests.get(url, params)
        if resp.status_code == [401, 403]:
            logger.error(f"Response when validating Steam API Key: {resp.status_code}")
            logger.debug(f"Full response: {resp}")
            raise InvalidSteamAPIKeyException("Invalid steam API key")
        if resp.status_code == 400:
            logger.error(f"Response: {resp.status_code}")
            logger.debug(f"Full response: {resp}")
            raise InvalidSteamIDException(f"Invalid steam ID: {steam_id}")
        resp.raise_for_status()
