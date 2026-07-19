import json
from datetime import datetime
from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, RootModel

from core.ld import format_ld_date, parse_ld_date


# Poll composition limits, advertised via /api/v1/instance and enforced
# when creating or editing a poll through the client API.
POLL_MAX_OPTIONS = 42
POLL_MAX_OPTION_CHARS = 50
POLL_MIN_EXPIRATION = 300
POLL_MAX_EXPIRATION = 2629746


def vote_value(option_name: str) -> str:
    """
    The stored form of a voted option: remote polls may have names longer
    than local limits allow, but PostInteraction.value is a 50-char column.
    """
    return option_name[:POLL_MAX_OPTION_CHARS]


class BasePostDataType(BaseModel):
    pass


class QuestionOption(BaseModel):
    name: str
    type: Literal["Note"] = "Note"
    votes: int = 0

    def __init__(self, **data) -> None:
        if "name" not in data:
            data["name"] = list((data.get("nameMap", {}) or {"": ""}).values())[0]
        # JSON-LD canonicalisation may yield a list when `name` and `nameMap`
        # carry the same value (e.g. pl.fediverse.pl polls).
        if isinstance(data["name"], list):
            data["name"] = data["name"][0] if data["name"] else ""
        data["votes"] = data.get("votes", data.get("replies", {}).get("totalItems", 0))
        super().__init__(**data)


class QuestionData(BasePostDataType):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: Literal["Question"]
    mode: Literal["oneOf", "anyOf"] | None = None
    options: list[QuestionOption] | None = None
    voter_count: int = Field(0, alias="http://joinmastodon.org/ns#votersCount")
    end_time: datetime | None = Field(None, alias="endTime")
    closed: datetime | None = None
    hide_totals: bool = False
    # Internal bookkeeping, never sent over AP or the client API:
    # tally snapshot at last federated Update, and when a remote poll
    # was last re-fetched from its origin.
    last_distributed_tally: str | None = None
    last_fetched: datetime | None = None

    def __init__(self, **data) -> None:
        data["voter_count"] = data.get(
            "voter_count", data.get("votersCount", data.get("toot:votersCount", 0))
        )
        if "mode" not in data:
            data["mode"] = "anyOf" if "anyOf" in data else "oneOf"
        if "options" not in data:
            options = data.pop("anyOf", None)
            if not options:
                options = data.pop("oneOf", None)
            data["options"] = options
        # Some servers signal a finished poll with a boolean `closed`
        # rather than a datetime (Mastodon treats any truthy value as
        # "closed now"), and invalid datetimes (e.g. hour 26) have been
        # seen in the wild - drop those rather than failing the post.
        closed = data.get("closed")
        if isinstance(closed, bool):
            data["closed"] = timezone.now() if closed else None
        elif isinstance(closed, str):
            try:
                data["closed"] = parse_ld_date(closed)
            except ValueError, OverflowError:
                data["closed"] = None
        super().__init__(**data)

    @property
    def effective_end_time(self) -> datetime | None:
        return self.closed or self.end_time

    @property
    def is_expired(self) -> bool:
        end_time = self.effective_end_time
        return bool(end_time and timezone.now() >= end_time)

    @property
    def tally(self) -> str:
        """
        Snapshot of the current vote counts, used to detect when a new
        federated Update is due.
        """
        votes = ":".join(str(option.votes) for option in self.options or [])
        return f"{self.voter_count}:{votes}"

    def to_mastodon_json(self, post, identity=None):
        from activities.models import PostInteraction

        multiple = self.mode == "anyOf"
        expired = self.is_expired
        value = {
            "id": str(post.pk),
            "expires_at": None,
            "expired": expired,
            "multiple": multiple,
            "votes_count": 0,
            "voters_count": self.voter_count,
            "voted": False,
            "own_votes": [],
            "options": [],
            "emojis": [],
        }

        if self.effective_end_time:
            value["expires_at"] = format_ld_date(self.effective_end_time)

        # Per-option tallies stay hidden until the poll ends when the
        # author asked for hidden totals.
        totals_hidden = self.hide_totals and not expired
        options = self.options or []
        option_map = {}
        for index, option in enumerate(options):
            value["options"].append(
                {
                    "title": option.name,
                    "votes_count": None if totals_hidden else option.votes,
                }
            )
            value["votes_count"] += option.votes
            option_map[vote_value(option.name)] = index

        if identity:
            votes = post.interactions.filter(
                identity=identity,
                type=PostInteraction.Types.vote,
            )
            value["voted"] = post.author == identity or votes.exists()
            value["own_votes"] = [
                option_map[vote.value] for vote in votes if vote.value in option_map
            ]

        return value


class ArticleData(BasePostDataType):
    model_config = ConfigDict(extra="ignore")

    type: Literal["Article"]
    attributed_to: str | None = Field(None, alias="attributedTo")


PostDataType = QuestionData | ArticleData


class PostTypeData(RootModel):
    root: PostDataType = Field(discriminator="type")


class PostTypeDataEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, BasePostDataType):
            return o.model_dump()
        elif isinstance(o, datetime):
            return o.isoformat()
        return json.JSONEncoder.default(self, o)


class PostTypeDataDecoder(json.JSONDecoder):
    def decode(self, *args, **kwargs):
        s = super().decode(*args, **kwargs)
        if isinstance(s, dict) and "type" in s:
            return PostTypeData.model_validate(s).root
        return s
