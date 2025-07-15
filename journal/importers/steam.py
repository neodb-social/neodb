import logging
from datetime import datetime, timedelta
from typing import Iterable, List, TypedDict

import pytz
import requests
from django.utils import timezone
from requests import HTTPError

from catalog.common.downloaders import DownloadError
from catalog.common.models import IdType, Item
from catalog.common.sites import SiteManager
from journal.models.common import VisibilityType
from journal.models.mark import Mark
from journal.models.shelf import ShelfType
from users.models import Task

logger = logging.getLogger(__name__)

# with reference to
# - https://developer.valvesoftware.com/wiki/Steam_Web_API
# - https://steamapi.xpaw.me/
#
# Get played (owned) games from IPlayerService.GetOwnedGames
# Get wishlist games from IWishlistService/GetWishlist
# TODO: asynchronous item loading
# TODO: implement get_time_to_beat with igdb
# TODO: log: use logging, loguru, or auditlog?

STEAM_API_BASE_URL = "https://api.steampowered.com"


class RawGameMark(TypedDict):
    app_id: str
    shelf_type: ShelfType
    created_time: datetime
    raw_entry: dict


class SteamImporter(Task):
    class MetaData(TypedDict):
        shelf_type_reversion: bool  # allow cases like PROGRESS to WISHLIST
        fetch_wishlist: bool
        fetch_owned: bool
        last_play_to_ctime: bool  # False: use current time
        shelf_filter: List[ShelfType]
        owned_filter: str
        ignored_appids: List[str]
        steam_tz: str
        total: int
        skipped: int
        processed: int
        failed: int
        imported: int
        visibility: VisibilityType
        failed_appids: List[str]
        steam_apikey: str
        steam_id: str

    TaskQueue = "import"
    DefaultMetadata: MetaData = {
        "shelf_type_reversion": False,
        "fetch_wishlist": True,
        "fetch_owned": True,
        "last_play_to_ctime": True,
        "shelf_filter": [
            ShelfType.COMPLETE,
            ShelfType.DROPPED,
            ShelfType.PROGRESS,
            ShelfType.WISHLIST,
        ],
        "owned_filter": "played_free",
        "ignored_appids": [],
        "steam_tz": "UTC",
        "total": 0,
        "skipped": 0,
        "processed": 0,
        "failed": 0,
        "imported": 0,
        "visibility": VisibilityType.Private,
        "failed_appids": [],
        "steam_apikey": "",
        "steam_id": "",
    }
    metadata: MetaData

    def run(self):
        """
        Run task: fetch wishlist and/or owned games and import marks
        """
        logger.debug("Start importing")

        fetched_raw_marks: List[RawGameMark] = []
        if self.metadata["fetch_wishlist"]:
            fetched_raw_marks.extend(self.get_wishlist_games())
        if self.metadata["fetch_owned"]:
            fetched_raw_marks.extend(self.get_owned_games())
        # filter out by shelftype and appid
        fetched_raw_marks = [
            raw_mark
            for raw_mark in fetched_raw_marks
            if (
                raw_mark["shelf_type"] in self.metadata["shelf_filter"]
                and raw_mark["app_id"] not in self.metadata["ignored_appids"]
            )
        ]
        self.metadata["total"] = len(fetched_raw_marks)
        logger.debug(f"{self.metadata['total']} raw marks fetched: {fetched_raw_marks}")

        self.import_marks(fetched_raw_marks)
        self.message = f"""
        Steam importing complete, total: {self.metadata["total"]}, processed: {self.metadata["processed"]}, imported: {self.metadata["imported"]}, failed: {self.metadata["failed"]}, skipped: {self.metadata["skipped"]}
        """
        self.save()

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
                self.metadata["failed"] += 1
                self.metadata["processed"] += 1
                self.metadata["failed_appids"].append(raw_mark["raw_entry"]["appid"])
                continue
            logger.debug(f"Item fetched: {item}")

            mark = Mark(self.user.identity, item)
            logger.debug(f"Mark fetched: {mark}")

            if (
                not self.metadata[
                    "shelf_type_reversion"
                ]  # if reversion is not allowed, then skip marked entry with reversion
                and (
                    mark.shelf_type == ShelfType.COMPLETE
                    or (
                        mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
                        and raw_mark["shelf_type"] == ShelfType.WISHLIST
                    )
                )
            ):
                logger.info(f"Game {mark.item.title} is already marked, skipping.")
                self.metadata["skipped"] += 1
            else:
                mark.update(
                    shelf_type=raw_mark["shelf_type"],
                    visibility=self.metadata["visibility"],
                    created_time=raw_mark["created_time"].replace(
                        tzinfo=pytz.timezone(self.metadata["steam_tz"])
                    ),
                )
                logger.debug(f"Mark updated: {mark}")
                self.metadata["imported"] += 1

            self.metadata["processed"] += 1

    # NOTE: undocumented api used
    def get_wishlist_games(self) -> Iterable[RawGameMark]:
        """
        From IWishlistService/GetWishlist, fetch wishlist of `steam_id` in self.metadata, and convert to RawGameMarks

        :return: Parsed list of raw game marked
        """
        url = f"{STEAM_API_BASE_URL}/IWishlistService/GetWishlist/v1/"
        params = {
            "key": self.metadata["steam_apikey"],
            "steamid": self.metadata["steam_id"],
        }

        res = requests.get(url, params)
        if res.status_code != 200:
            logger.error("Network error when getting wishlist.")

        for entry in res.json()["response"]["items"]:
            created_time = datetime.fromtimestamp(entry["date_added"])
            yield {
                "app_id": str(entry["appid"]),
                "shelf_type": ShelfType.WISHLIST,
                "created_time": created_time,
                "raw_entry": entry,
            }

    def get_owned_games(
        self, estimate_shelf_type: bool = True
    ) -> Iterable[RawGameMark]:
        """
        From IPlayerService.GetOwnedGames, fetch owned games of `steam_id` in self.metadata, and convert to RawGameMarks

        :return: Parsed list of raw game marked
        """
        url = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v1/"
        params = {
            "key": self.metadata["steam_apikey"],
            "steamid": str(self.metadata["steam_id"]),
            "include_appinfo": False,
            "include_played_free_games": self.metadata["owned_filter"] != "no_free",
            "appids_filter": [],
            "include_free_sub": self.metadata["owned_filter"] == "all_free",
            "language": "en",  # appinfo not used, so this is no use
            "include_extended_appinfo": False,
        }

        res = requests.get(url, params)
        if res.status_code != 200:
            logger.error("Network error when getting owned games.")

        for entry in res.json()["response"]["games"]:
            rtime_last_played = datetime.fromtimestamp(entry["rtime_last_played"])
            playtime_forever = entry["playtime_forever"]
            app_id = str(entry["appid"])
            if estimate_shelf_type:
                shelf_type = SteamImporter.estimate_shelf_type(
                    playtime_forever, rtime_last_played, app_id
                )
            else:
                shelf_type = ShelfType.COMPLETE
            # FIX: consider such case:
            # the game is purchased and never played, so rtime is 0, and we have no wishlist
            created_time = (
                rtime_last_played
                if self.metadata["last_play_to_ctime"]
                else timezone.now()
            )
            yield {
                "app_id": app_id,
                "shelf_type": shelf_type,
                "created_time": created_time,
                "raw_entry": entry,
            }

    def get_item_by_id(
        self, app_id: str, id_type: IdType = IdType.Steam
    ) -> Item | None:
        site = SiteManager.get_site_by_id(id_type, app_id)
        if not site:
            raise ValueError(f"{id_type} not in site registry")
        item = site.get_item()
        if item:
            return item

        logger.debug(f"Fetching game {app_id} from steam")
        try:
            site.get_resource_ready()
            item = site.get_item()
        except DownloadError as e:
            logger.error(f"Fail to fetch {e.url}")
            item = None
        except Exception as e:
            logger.error(f"Unexcepted error when getting item from appid {app_id}")
            logger.exception(e)
            item = None
        return item

    @classmethod
    def estimate_shelf_type(
        cls, playtime_forever: int, last_played: datetime, app_id: str
    ):
        played_long_enough = (
            playtime_forever / SteamImporter.get_how_long_to_beat(app_id) > 0.75
        )
        never_played = playtime_forever == 0 and last_played == datetime.fromtimestamp(
            0
        )
        playing = datetime.now() - last_played < timedelta(weeks=2)
        # ever played in 2 weeks

        if never_played:
            return ShelfType.WISHLIST  # we all have games purchased and never played...
        elif playing:
            return ShelfType.PROGRESS
        elif played_long_enough:
            return ShelfType.COMPLETE
        else:
            return ShelfType.DROPPED

    @classmethod
    def validate_apikey(cls, steam_apikey: str) -> bool:
        logger.debug(f"Validating api key: {steam_apikey}")
        url = f"{STEAM_API_BASE_URL}/ISteamWebAPIUtil/GetSupportedAPIList/v1/"
        params = {
            "key": steam_apikey,
        }
        try:
            interfaces = requests.get(url, params).json()["apilist"]["interfaces"]
            method_names = [
                method["name"]
                for interface in interfaces
                for method in interface["methods"]
            ]
            # logger.debug(f"Methods available: {method_names}")
            return "GetOwnedGames" in method_names
        except HTTPError as e:
            if e.response.status_code in [401, 403]:
                logger.error("Invalid apikey")
                return False
            else:
                raise e

    @classmethod
    def validate_userid(cls, steam_apikey: str, steam_id: str) -> bool:
        logger.debug(f"Validating steam_id: {steam_id}")
        url = f"{STEAM_API_BASE_URL}/ISteamUser/GetPlayerSummaries/v2/"
        params = {
            "key": steam_apikey,
            "steamids": steam_id,
        }
        try:
            players = requests.get(url, params).json()["response"]["players"]
            return players != []
        except HTTPError as e:
            if e.response.status_code == [401, 403]:
                logger.error("Invalid apikey")
                return False
            else:
                raise e

    # TODO: Implement get_how_long_to_beat:
    # Such data are available in HowLongToBeat.com and igdb, however
    # 1. time_to_beat can be considered a potential metadata of Game item,
    # 2. though sites are primarily used to scrape data, it seems better to extend them as api interface
    # 3. if 1 happens, the time to beat data shall be fetched from Game item, instead of this method
    @classmethod
    def get_how_long_to_beat(cls, steamid: str) -> int:
        return 20
        ...
