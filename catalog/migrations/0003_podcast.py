# Generated by Django 3.2.16 on 2023-02-02 03:47

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="externalresource",
            name="id_type",
            field=models.CharField(
                choices=[
                    ("wikidata", "维基数据"),
                    ("isbn10", "ISBN10"),
                    ("isbn", "ISBN"),
                    ("asin", "ASIN"),
                    ("issn", "ISSN"),
                    ("cubn", "统一书号"),
                    ("isrc", "ISRC"),
                    ("gtin", "GTIN UPC EAN码"),
                    ("rss", "RSS Feed URL"),
                    ("imdb", "IMDb"),
                    ("tmdb_tv", "TMDB剧集"),
                    ("tmdb_tvseason", "TMDB剧集"),
                    ("tmdb_tvepisode", "TMDB剧集"),
                    ("tmdb_movie", "TMDB电影"),
                    ("goodreads", "Goodreads"),
                    ("goodreads_work", "Goodreads著作"),
                    ("googlebooks", "谷歌图书"),
                    ("doubanbook", "豆瓣读书"),
                    ("doubanbook_work", "豆瓣读书著作"),
                    ("doubanmovie", "豆瓣电影"),
                    ("doubanmusic", "豆瓣音乐"),
                    ("doubangame", "豆瓣游戏"),
                    ("doubandrama", "豆瓣舞台剧"),
                    ("bandcamp", "Bandcamp"),
                    ("spotify_album", "Spotify专辑"),
                    ("spotify_show", "Spotify播客"),
                    ("discogs_release", "Discogs Release"),
                    ("discogs_master", "Discogs Master"),
                    ("musicbrainz", "MusicBrainz ID"),
                    ("doubanbook_author", "豆瓣读书作者"),
                    ("doubanmovie_celebrity", "豆瓣电影影人"),
                    ("goodreads_author", "Goodreads作者"),
                    ("spotify_artist", "Spotify艺术家"),
                    ("tmdb_person", "TMDB影人"),
                    ("igdb", "IGDB游戏"),
                    ("steam", "Steam游戏"),
                    ("bangumi", "Bangumi"),
                    ("apple_podcast", "苹果播客"),
                ],
                max_length=50,
                verbose_name="IdType of the source site",
            ),
        ),
        migrations.AlterField(
            model_name="itemlookupid",
            name="id_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("wikidata", "维基数据"),
                    ("isbn10", "ISBN10"),
                    ("isbn", "ISBN"),
                    ("asin", "ASIN"),
                    ("issn", "ISSN"),
                    ("cubn", "统一书号"),
                    ("isrc", "ISRC"),
                    ("gtin", "GTIN UPC EAN码"),
                    ("rss", "RSS Feed URL"),
                    ("imdb", "IMDb"),
                    ("tmdb_tv", "TMDB剧集"),
                    ("tmdb_tvseason", "TMDB剧集"),
                    ("tmdb_tvepisode", "TMDB剧集"),
                    ("tmdb_movie", "TMDB电影"),
                    ("goodreads", "Goodreads"),
                    ("goodreads_work", "Goodreads著作"),
                    ("googlebooks", "谷歌图书"),
                    ("doubanbook", "豆瓣读书"),
                    ("doubanbook_work", "豆瓣读书著作"),
                    ("doubanmovie", "豆瓣电影"),
                    ("doubanmusic", "豆瓣音乐"),
                    ("doubangame", "豆瓣游戏"),
                    ("doubandrama", "豆瓣舞台剧"),
                    ("bandcamp", "Bandcamp"),
                    ("spotify_album", "Spotify专辑"),
                    ("spotify_show", "Spotify播客"),
                    ("discogs_release", "Discogs Release"),
                    ("discogs_master", "Discogs Master"),
                    ("musicbrainz", "MusicBrainz ID"),
                    ("doubanbook_author", "豆瓣读书作者"),
                    ("doubanmovie_celebrity", "豆瓣电影影人"),
                    ("goodreads_author", "Goodreads作者"),
                    ("spotify_artist", "Spotify艺术家"),
                    ("tmdb_person", "TMDB影人"),
                    ("igdb", "IGDB游戏"),
                    ("steam", "Steam游戏"),
                    ("bangumi", "Bangumi"),
                    ("apple_podcast", "苹果播客"),
                ],
                max_length=50,
                verbose_name="源网站",
            ),
        ),
        migrations.CreateModel(
            name="PodcastEpisode",
            fields=[
                ("description_html", models.TextField(null=True)),
                ("cover_url", models.CharField(max_length=1000, null=True)),
                ("media_url", models.CharField(max_length=1000, null=True)),
                ("guid", models.CharField(max_length=1000, null=True)),
                ("pub_date", models.DateTimeField()),
                ("duration", models.PositiveIntegerField(null=True)),
                (
                    "program",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="episodes",
                        to="catalog.podcast",
                    ),
                ),
                ("link", models.CharField(max_length=1000, null=True)),
                (
                    "item_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        default=0,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="catalog.item",
                    ),
                ),
            ],
            options={
                "unique_together": {("program", "guid")},
                "index_together": {("program", "pub_date")},
            },
        ),
    ]
