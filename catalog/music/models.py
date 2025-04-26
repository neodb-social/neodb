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
    PrimaryLookupIdDescriptor,
    jsondata,
)
from catalog.common.models import LIST_OF_ONE_PLUS_STR_SCHEMA, LIST_OF_STR_SCHEMA


class AlbumInSchema(ItemInSchema):
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
    schema = AlbumSchema
    url_path = "album"
    category = ItemCategory.Music
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
    artist = jsondata.JSONField(
        verbose_name=_("artist"),
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
    company = jsondata.JSONField(
        verbose_name=_("publisher"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    track_list = jsondata.TextField(_("tracks"), blank=True)
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
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        if self.barcode:
            d["lookup_id"] = [str(self.barcode)]
        d["people"] = self.artist or []
        d["company"] = self.company or []
        d["date"] = (
            [int(self.release_date.strftime("%Y%m%d"))] if self.release_date else []
        )
        d["genre"] = self.genre or []  # type:ignore
        d["format"] = [self.album_type] if self.album_type else []
        d["format"] += [self.media] if self.media else []
        return d
