# Generated by Django 4.2.13 on 2024-06-01 05:38

import functools

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import takahe.models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Domain",
            fields=[
                (
                    "domain",
                    models.CharField(max_length=250, primary_key=True, serialize=False),
                ),
                (
                    "service_domain",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=250,
                        null=True,
                        unique=True,
                    ),
                ),
                ("state", models.CharField(default="outdated", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("nodeinfo", models.JSONField(blank=True, null=True)),
                ("local", models.BooleanField()),
                ("blocked", models.BooleanField(default=False)),
                ("public", models.BooleanField(default=False)),
                ("default", models.BooleanField(default=False)),
                ("notes", models.TextField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "users_domain",
            },
        ),
        migrations.CreateModel(
            name="Emoji",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("shortcode", models.SlugField(max_length=100)),
                ("local", models.BooleanField(default=True)),
                ("public", models.BooleanField(null=True)),
                (
                    "object_uri",
                    models.CharField(
                        blank=True, max_length=500, null=True, unique=True
                    ),
                ),
                ("mimetype", models.CharField(max_length=200)),
                ("file", models.ImageField(blank=True, null=True, upload_to="")),
                ("remote_url", models.CharField(blank=True, max_length=500, null=True)),
                ("category", models.CharField(blank=True, max_length=100, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "domain",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="takahe.domain",
                    ),
                ),
            ],
            options={
                "db_table": "activities_emoji",
            },
        ),
        migrations.CreateModel(
            name="Hashtag",
            fields=[
                (
                    "hashtag",
                    models.SlugField(max_length=100, primary_key=True, serialize=False),
                ),
                (
                    "name_override",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("public", models.BooleanField(null=True)),
                ("state", models.CharField(default="outdated", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("stats", models.JSONField(blank=True, null=True)),
                ("stats_updated", models.DateTimeField(blank=True, null=True)),
                ("aliases", models.JSONField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "activities_hashtag",
            },
        ),
        migrations.CreateModel(
            name="Identity",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=takahe.models.Snowflake.generate_identity,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("actor_uri", models.CharField(max_length=500, unique=True)),
                ("state", models.CharField(default="outdated", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("state_next_attempt", models.DateTimeField(blank=True, null=True)),
                (
                    "state_locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("local", models.BooleanField(db_index=True)),
                ("username", models.CharField(blank=True, max_length=500, null=True)),
                (
                    "name",
                    models.CharField(
                        blank=True,
                        max_length=500,
                        null=True,
                        verbose_name="Display Name",
                    ),
                ),
                (
                    "summary",
                    models.TextField(blank=True, null=True, verbose_name="Bio"),
                ),
                (
                    "manually_approves_followers",
                    models.BooleanField(
                        default=False, verbose_name="Manually approve new followers"
                    ),
                ),
                (
                    "discoverable",
                    models.BooleanField(
                        default=True,
                        verbose_name="Include profile and posts in search and discovery",
                    ),
                ),
                (
                    "profile_uri",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                ("inbox_uri", models.CharField(blank=True, max_length=500, null=True)),
                (
                    "shared_inbox_uri",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                ("outbox_uri", models.CharField(blank=True, max_length=500, null=True)),
                ("icon_uri", models.CharField(blank=True, max_length=500, null=True)),
                ("image_uri", models.CharField(blank=True, max_length=500, null=True)),
                (
                    "followers_uri",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                (
                    "following_uri",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                (
                    "featured_collection_uri",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                ("actor_type", models.CharField(default="person", max_length=100)),
                (
                    "icon",
                    models.ImageField(
                        blank=True,
                        null=True,
                        storage=takahe.models.upload_store,
                        upload_to=functools.partial(
                            takahe.models.upload_namer, *("profile_images",), **{}
                        ),
                        verbose_name="Profile picture",
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        storage=takahe.models.upload_store,
                        upload_to=functools.partial(
                            takahe.models.upload_namer, *("background_images",), **{}
                        ),
                        verbose_name="Header picture",
                    ),
                ),
                ("metadata", models.JSONField(blank=True, null=True)),
                ("pinned", models.JSONField(blank=True, null=True)),
                ("sensitive", models.BooleanField(default=False)),
                (
                    "restriction",
                    models.IntegerField(
                        choices=[(0, "None"), (1, "Limited"), (2, "Blocked")],
                        db_index=True,
                        default=0,
                    ),
                ),
                ("admin_notes", models.TextField(blank=True, null=True)),
                ("private_key", models.TextField(blank=True, null=True)),
                ("public_key", models.TextField(blank=True, null=True)),
                ("public_key_id", models.TextField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("fetched", models.DateTimeField(blank=True, null=True)),
                ("deleted", models.DateTimeField(blank=True, null=True)),
                (
                    "domain",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="identities",
                        to="takahe.domain",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "identities",
                "db_table": "users_identity",
            },
        ),
        migrations.CreateModel(
            name="InboxMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("message", models.JSONField()),
                ("state", models.CharField(default="received", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "users_inboxmessage",
            },
        ),
        migrations.CreateModel(
            name="Invite",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("token", models.CharField(max_length=500, unique=True)),
                ("note", models.TextField(blank=True, null=True)),
                ("uses", models.IntegerField(blank=True, null=True)),
                ("expires", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "users_invite",
            },
        ),
        migrations.CreateModel(
            name="Post",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=takahe.models.Snowflake.generate_post,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("state", models.CharField(default="new", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("local", models.BooleanField()),
                (
                    "object_uri",
                    models.CharField(
                        blank=True, max_length=2048, null=True, unique=True
                    ),
                ),
                (
                    "visibility",
                    models.IntegerField(
                        choices=[
                            (0, "Public"),
                            (4, "Local Only"),
                            (1, "Unlisted"),
                            (2, "Followers"),
                            (3, "Mentioned"),
                        ],
                        default=0,
                    ),
                ),
                ("content", models.TextField()),
                ("language", models.CharField(blank=True, default="")),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("Article", "Article"),
                            ("Audio", "Audio"),
                            ("Event", "Event"),
                            ("Image", "Image"),
                            ("Note", "Note"),
                            ("Page", "Page"),
                            ("Question", "Question"),
                            ("Video", "Video"),
                        ],
                        default="Note",
                        max_length=20,
                    ),
                ),
                ("type_data", models.JSONField(blank=True, null=True)),
                ("sensitive", models.BooleanField(default=False)),
                ("summary", models.TextField(blank=True, null=True)),
                ("url", models.CharField(blank=True, max_length=2048, null=True)),
                (
                    "in_reply_to",
                    models.CharField(
                        blank=True, db_index=True, max_length=500, null=True
                    ),
                ),
                ("hashtags", models.JSONField(blank=True, null=True)),
                ("stats", models.JSONField(blank=True, null=True)),
                ("published", models.DateTimeField(default=django.utils.timezone.now)),
                ("edited", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="posts",
                        to="takahe.identity",
                    ),
                ),
                (
                    "emojis",
                    models.ManyToManyField(
                        blank=True, related_name="posts_using_emoji", to="takahe.emoji"
                    ),
                ),
                (
                    "mentions",
                    models.ManyToManyField(
                        blank=True,
                        related_name="posts_mentioning",
                        to="takahe.identity",
                    ),
                ),
                (
                    "to",
                    models.ManyToManyField(
                        blank=True, related_name="posts_to", to="takahe.identity"
                    ),
                ),
            ],
            options={
                "db_table": "activities_post",
            },
        ),
        migrations.CreateModel(
            name="PostInteraction",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=takahe.models.Snowflake.generate_post_interaction,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("state", models.CharField(default="new", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                (
                    "object_uri",
                    models.CharField(
                        blank=True, max_length=500, null=True, unique=True
                    ),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("like", "Like"),
                            ("boost", "Boost"),
                            ("vote", "Vote"),
                            ("pin", "Pin"),
                        ],
                        max_length=100,
                    ),
                ),
                ("value", models.CharField(blank=True, max_length=50, null=True)),
                ("published", models.DateTimeField(default=django.utils.timezone.now)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interactions",
                        to="takahe.identity",
                    ),
                ),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interactions",
                        to="takahe.post",
                    ),
                ),
            ],
            options={
                "db_table": "activities_postinteraction",
            },
        ),
        migrations.CreateModel(
            name="Relay",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("inbox_uri", models.CharField(max_length=500, unique=True)),
                ("state", models.CharField(default="new", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("state_next_attempt", models.DateTimeField(blank=True, null=True)),
                (
                    "state_locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "users_relay",
            },
        ),
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("admin", models.BooleanField(default=False)),
                ("moderator", models.BooleanField(default=False)),
                ("banned", models.BooleanField(default=False)),
                ("deleted", models.BooleanField(default=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("last_seen", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "users_user",
            },
        ),
        migrations.CreateModel(
            name="PostAttachment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("state", models.CharField(default="new", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("mimetype", models.CharField(max_length=200)),
                (
                    "file",
                    models.FileField(
                        blank=True,
                        null=True,
                        storage=takahe.models.upload_store,
                        upload_to=functools.partial(
                            takahe.models.upload_namer, *("attachments",), **{}
                        ),
                    ),
                ),
                (
                    "thumbnail",
                    models.ImageField(
                        blank=True,
                        null=True,
                        storage=takahe.models.upload_store,
                        upload_to=functools.partial(
                            takahe.models.upload_namer,
                            *("attachment_thumbnails",),
                            **{},
                        ),
                    ),
                ),
                ("remote_url", models.CharField(blank=True, max_length=500, null=True)),
                ("name", models.TextField(blank=True, null=True)),
                ("width", models.IntegerField(blank=True, null=True)),
                ("height", models.IntegerField(blank=True, null=True)),
                ("focal_x", models.FloatField(blank=True, null=True)),
                ("focal_y", models.FloatField(blank=True, null=True)),
                ("blurhash", models.TextField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="takahe.identity",
                    ),
                ),
                (
                    "post",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="takahe.post",
                    ),
                ),
            ],
            options={
                "db_table": "activities_postattachment",
            },
        ),
        migrations.AddField(
            model_name="identity",
            name="users",
            field=models.ManyToManyField(
                blank=True, related_name="identities", to="takahe.user"
            ),
        ),
        migrations.CreateModel(
            name="FanOut",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("state", models.CharField(default="outdated", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("post", "Post"),
                            ("post_edited", "Post Edited"),
                            ("post_deleted", "Post Deleted"),
                            ("interaction", "Interaction"),
                            ("undo_interaction", "Undo Interaction"),
                            ("identity_edited", "Identity Edited"),
                            ("identity_deleted", "Identity Deleted"),
                            ("identity_created", "Identity Created"),
                            ("identity_moved", "Identity Moved"),
                        ],
                        max_length=100,
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fan_outs",
                        to="takahe.identity",
                    ),
                ),
                (
                    "subject_identity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subject_fan_outs",
                        to="takahe.identity",
                    ),
                ),
                (
                    "subject_post",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fan_outs",
                        to="takahe.post",
                    ),
                ),
                (
                    "subject_post_interaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fan_outs",
                        to="takahe.postinteraction",
                    ),
                ),
            ],
            options={
                "db_table": "activities_fanout",
            },
        ),
        migrations.AddField(
            model_name="domain",
            name="users",
            field=models.ManyToManyField(
                blank=True, related_name="domains", to="takahe.user"
            ),
        ),
        migrations.CreateModel(
            name="Block",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("state", models.CharField(default="new", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("uri", models.CharField(blank=True, max_length=500, null=True)),
                ("mute", models.BooleanField()),
                ("include_notifications", models.BooleanField(default=False)),
                ("expires", models.DateTimeField(blank=True, null=True)),
                ("note", models.TextField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbound_blocks",
                        to="takahe.identity",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbound_blocks",
                        to="takahe.identity",
                    ),
                ),
            ],
            options={
                "db_table": "users_block",
            },
        ),
        migrations.CreateModel(
            name="Announcement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("text", models.TextField()),
                ("published", models.BooleanField(default=False)),
                ("start", models.DateTimeField(blank=True, null=True)),
                ("end", models.DateTimeField(blank=True, null=True)),
                ("include_unauthenticated", models.BooleanField(default=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("seen", models.ManyToManyField(blank=True, to="takahe.user")),
            ],
            options={
                "db_table": "users_announcement",
            },
        ),
        migrations.CreateModel(
            name="TimelineEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("post", "Post"),
                            ("boost", "Boost"),
                            ("mentioned", "Mentioned"),
                            ("liked", "Liked"),
                            ("followed", "Followed"),
                            ("follow_requested", "Follow Requested"),
                            ("boosted", "Boosted"),
                            ("announcement", "Announcement"),
                            ("identity_created", "Identity Created"),
                        ],
                        max_length=100,
                    ),
                ),
                ("published", models.DateTimeField(default=django.utils.timezone.now)),
                ("seen", models.BooleanField(default=False)),
                ("dismissed", models.BooleanField(default=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timeline_events",
                        to="takahe.identity",
                    ),
                ),
                (
                    "subject_identity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timeline_events_about_us",
                        to="takahe.identity",
                    ),
                ),
                (
                    "subject_post",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timeline_events",
                        to="takahe.post",
                    ),
                ),
                (
                    "subject_post_interaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timeline_events",
                        to="takahe.postinteraction",
                    ),
                ),
            ],
            options={
                "db_table": "activities_timelineevent",
                "indexes": [
                    models.Index(
                        fields=["identity", "type", "subject_post", "subject_identity"],
                        name="activities__identit_0b93c3_idx",
                    ),
                    models.Index(
                        fields=["identity", "type", "subject_identity"],
                        name="activities__identit_cc2290_idx",
                    ),
                    models.Index(
                        fields=["identity", "created"],
                        name="activities__identit_872fbb_idx",
                    ),
                ],
            },
        ),
        migrations.AlterUniqueTogether(
            name="identity",
            unique_together={("username", "domain")},
        ),
        migrations.CreateModel(
            name="Follow",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        default=takahe.models.Snowflake.generate_follow,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "boosts",
                    models.BooleanField(
                        default=True, help_text="Also follow boosts from this user"
                    ),
                ),
                ("uri", models.CharField(blank=True, max_length=500, null=True)),
                ("note", models.TextField(blank=True, null=True)),
                ("state", models.CharField(default="unrequested", max_length=100)),
                ("state_changed", models.DateTimeField(auto_now_add=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbound_follows",
                        to="takahe.identity",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbound_follows",
                        to="takahe.identity",
                    ),
                ),
            ],
            options={
                "db_table": "users_follow",
                "unique_together": {("source", "target")},
            },
        ),
        migrations.CreateModel(
            name="Config",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key", models.CharField(max_length=500)),
                ("json", models.JSONField(blank=True, null=True)),
                ("image", models.ImageField(blank=True, null=True, upload_to="")),
                (
                    "domain",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configs",
                        to="takahe.domain",
                    ),
                ),
                (
                    "identity",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configs",
                        to="takahe.identity",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configs",
                        to="takahe.user",
                    ),
                ),
            ],
            options={
                "db_table": "core_config",
                "unique_together": {("key", "user", "identity", "domain")},
            },
        ),
    ]
