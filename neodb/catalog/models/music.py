from django.utils.translation import gettext_lazy as _
from ninja import Field

from common.models import (
    coerce_album_duration,
    duration_to_seconds,
    normalize_album_types,
    normalize_media_formats,
    partial_date_to_int,
)

from .common import (
    LIST_OF_ONE_PLUS_STR_SCHEMA,
    LIST_OF_STR_SCHEMA,
    AlbumTypeListField,
    GenreListField,
    MediaFormatListField,
    jsondata,
)
from .item import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
)
from .people import PeopleRole
from .utils import canonicalize_release_date_key


class AlbumInSchema(ItemInSchema):
    genre: list[str]
    artist: list[str]
    company: list[str]
    length: int | None = None
    duration: int | None = Field(
        None, deprecated="Milliseconds; use `length` (seconds) instead."
    )
    release_date: str | None = None
    album_type: list[str]
    media_format: list[str]
    track_list: str | None = None
    # media is deprecated
    media: str | None = Field(None, deprecated="Use `media_format` (list) instead.")

    @staticmethod
    def resolve_length(obj: "Album") -> int | None:
        # numeric values are trusted as seconds; unit inference happens
        # only on legacy ingest (normalize_legacy_metadata)
        return duration_to_seconds(obj.length)

    @staticmethod
    def resolve_duration(obj: "Album") -> int | None:
        # older peers and clients read this as milliseconds
        seconds = duration_to_seconds(obj.length)
        return seconds * 1000 if seconds else None

    @staticmethod
    def resolve_album_type(obj: "Album") -> list[str]:
        # tolerate legacy free-text values not yet migrated
        return normalize_album_types(obj.album_type)

    @staticmethod
    def resolve_media_format(obj: "Album") -> list[str]:
        return normalize_media_formats(obj.media_format)

    @staticmethod
    def resolve_media(obj: "Album") -> str | None:
        formats = normalize_media_formats(obj.media_format)
        return ", ".join(formats) if formats else None

    @staticmethod
    def resolve_artist(obj: "Album") -> list[str]:
        return obj.credit_names_by_role("artist")

    @staticmethod
    def resolve_company(obj: "Album") -> list[str]:
        return obj.credit_names_by_role("record_label")


class AlbumSchema(AlbumInSchema, BaseSchema):
    barcode: str | None = None
    pass


class Album(Item):
    schema = AlbumSchema
    url_path = "album"
    category = ItemCategory.Music
    type = ItemType.Album

    available_roles = [
        PeopleRole.ARTIST,
        PeopleRole.PERFORMER,
        PeopleRole.COMPOSER,
        PeopleRole.PRODUCER,
        PeopleRole.RECORD_LABEL,
    ]
    CREDIT_FIELD_MAPPING = {
        "artist": "artist",
        "company": "record_label",
    }

    barcode = PrimaryLookupIdDescriptor(IdType.GTIN)
    douban_music = PrimaryLookupIdDescriptor(IdType.DoubanMusic)
    spotify_album = PrimaryLookupIdDescriptor(IdType.Spotify_Album)
    METADATA_COPY_LIST = [
        "localized_title",
        "artist",
        "company",
        "track_list",
        "localized_description",
        "album_type",
        "media_format",
        "disc_count",
        "genre",
        "release_date",
        "length",
        "bandcamp_album_id",
    ]
    release_date = jsondata.CharField(
        _("release date"),
        null=True,
        blank=True,
        max_length=10,
        help_text=_("YYYY, YYYY-MM or YYYY-MM-DD"),
    )
    length = jsondata.IntegerField(
        _("length"), null=True, blank=True, help_text=_("seconds")
    )
    artist = jsondata.JSONField(
        verbose_name=_("artist"),
        null=False,
        blank=False,
        default=list,
        schema=LIST_OF_ONE_PLUS_STR_SCHEMA,
    )
    genre = GenreListField(ItemCategory.Music)
    company = jsondata.JSONField(
        verbose_name=_("publisher"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    track_list = jsondata.TextField(_("tracks"), blank=True)
    album_type = AlbumTypeListField()
    media_format = MediaFormatListField()
    bandcamp_album_id = jsondata.CharField(blank=True, max_length=500)
    disc_count = jsondata.IntegerField(
        _("number of discs"), blank=True, default="", max_length=500
    )

    @property
    def display_album_types(self) -> list[str]:
        # tolerate legacy scalar values not yet migrated
        return normalize_album_types(self.album_type)

    @property
    def display_media_formats(self) -> list[str]:
        return normalize_media_formats(self.media_format)

    @classmethod
    def normalize_legacy_metadata(cls, metadata: dict) -> None:
        super().normalize_legacy_metadata(metadata)
        # Sources: federated peers running older code, ndjson restores,
        # and local rows that predate the unification.
        # - duration in milliseconds -> seconds
        # - media (free text) -> media_format (list of slugs)
        # - album_type free text -> list of slugs
        # current peers emit canonical seconds under "length"; the legacy
        # "duration" (ms) is only used when length is absent
        duration = metadata.pop("duration", None)
        if not metadata.get("length") and duration is not None:
            length = coerce_album_duration(duration)
            if length:
                metadata["length"] = length
        media = metadata.pop("media", None)
        if media and not metadata.get("media_format"):
            metadata["media_format"] = normalize_media_formats(media)
        if "album_type" in metadata:
            album_type = normalize_album_types(metadata["album_type"])
            if album_type:
                metadata["album_type"] = album_type
            else:
                metadata.pop("album_type", None)
        canonicalize_release_date_key(metadata)

    def get_embed_link(self) -> str | None:
        bandcamp_link = None
        youtube_link = None
        spotify_link = None
        apple_link = None
        for res in self.external_resources.all():
            if (
                res.id_type == IdType.Bandcamp.value
                and res.metadata.get("bandcamp_album_id")
                and not bandcamp_link
            ):
                bandcamp_link = f"https://bandcamp.com/EmbeddedPlayer/album={res.metadata.get('bandcamp_album_id')}/size=large/bgcol=ffffff/linkcol=19A2CA/artwork=small/transparent=true/"
            elif res.id_type == IdType.YouTubeMusic.value and not youtube_link:
                youtube_link = (
                    f"https://www.youtube.com/embed/videoseries?list={res.id_value}"
                )
            elif res.id_type == IdType.Spotify_Album.value and not spotify_link:
                spotify_link = res.url.replace(
                    "open.spotify.com/", "open.spotify.com/embed/"
                )
            elif res.id_type == IdType.AppleMusic.value and not apple_link:
                apple_link = res.url.replace(
                    "music.apple.com/", "embed.music.apple.com/us/"
                )
        return bandcamp_link or youtube_link or spotify_link or apple_link

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.GTIN,
            IdType.ISRC,
            IdType.Spotify_Album,
            IdType.Bandcamp,
            IdType.YouTubeMusic,
            IdType.DoubanMusic,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        if self.barcode:
            d["lookup_id"] = [str(self.barcode)]
        dt = partial_date_to_int(self.release_date)
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []
        d["format"] = list(self.album_type or []) + list(self.media_format or [])
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "MusicAlbum"

        artists = self.credit_names_by_role("artist")
        if artists:
            data["byArtist"] = [
                {"@type": "MusicGroup", "name": person} for person in artists
            ]

        if self.genre:
            data["genre"] = self.genre

        if self.track_list:
            # Simplified track list as text
            data["numTracks"] = len(self.track_list.split("\n"))

        labels = self.credit_names_by_role("record_label")
        if labels:
            data["publisher"] = {"@type": "Organization", "name": labels[0]}

        if self.release_date:
            data["datePublished"] = self.release_date

        seconds = duration_to_seconds(self.length)
        if seconds:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            data["duration"] = f"PT{hours}H{minutes}M{seconds}S"

        if self.barcode:
            data["gtin13"] = self.barcode

        return data
