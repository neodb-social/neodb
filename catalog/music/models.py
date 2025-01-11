from datetime import date

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from catalog.common import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
    jsondata,
)
from catalog.common.models import LIST_OF_ONE_PLUS_STR_SCHEMA


class AlbumInSchema(ItemInSchema):
    other_title: list[str]
    genre: list[str]
    artist: list[str]
    company: list[str]
    duration: int | None = None
    release_date: date | None = None
    track_list: str | None = None


class AlbumSchema(AlbumInSchema, BaseSchema):
    barcode: str | None = None
    pass


class Album(Item):
    type = ItemType.Album
    url_path = "album"
    category = ItemCategory.Music
    barcode = PrimaryLookupIdDescriptor(IdType.GTIN)
    douban_music = PrimaryLookupIdDescriptor(IdType.DoubanMusic)
    spotify_album = PrimaryLookupIdDescriptor(IdType.Spotify_Album)
    METADATA_COPY_LIST = [
        # "title",
        # "other_title",
        "localized_title",
        "artist",
        "company",
        "track_list",
        # "brief",
        "localized_description",
        "album_type",
        "media",
        "disc_count",
        "genre",
        "release_date",
        "duration",
        "bandcamp_album_id",
    ]
    release_date = jsondata.DateField(
        _("release date"), null=True, blank=True, help_text=_("YYYY-MM-DD")
    )
    duration = jsondata.IntegerField(
        _("length"), null=True, blank=True, help_text=_("milliseconds")
    )
    artist = jsondata.ArrayField(
        verbose_name=_("artist"),
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=False,
        blank=False,
        default=list,
        schema=LIST_OF_ONE_PLUS_STR_SCHEMA,
    )
    genre = jsondata.ArrayField(
        verbose_name=pgettext_lazy("music", "genre"),
        base_field=models.CharField(blank=True, default="", max_length=50),
        null=True,
        blank=True,
        default=list,
    )
    company = jsondata.ArrayField(
        models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("publisher"),
        null=True,
        blank=True,
        default=list,
    )
    track_list = jsondata.TextField(_("tracks"), blank=True)
    other_title = jsondata.ArrayField(
        verbose_name=_("other title"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    album_type = jsondata.CharField(_("album type"), blank=True, max_length=500)
    media = jsondata.CharField(_("media type"), blank=True, max_length=500)
    bandcamp_album_id = jsondata.CharField(blank=True, max_length=500)
    disc_count = jsondata.IntegerField(
        _("number of discs"), blank=True, default="", max_length=500
    )

    def get_embed_link(self):
        for res in self.external_resources.all():
            if res.id_type == IdType.Bandcamp.value and res.metadata.get(
                "bandcamp_album_id"
            ):
                return f"https://bandcamp.com/EmbeddedPlayer/album={res.metadata.get('bandcamp_album_id')}/size=large/bgcol=ffffff/linkcol=19A2CA/artwork=small/transparent=true/"
            if res.id_type == IdType.Spotify_Album.value:
                return res.url.replace("open.spotify.com/", "open.spotify.com/embed/")
            if res.id_type == IdType.AppleMusic.value:
                return res.url.replace("music.apple.com/", "embed.music.apple.com/us/")
        return None

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.GTIN,
            IdType.ISRC,
            IdType.Spotify_Album,
            IdType.Bandcamp,
            IdType.DoubanMusic,
        ]
        return [(i.value, i.label) for i in id_types]
