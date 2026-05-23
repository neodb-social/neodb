import re
import time
from datetime import date, datetime, timedelta

import urlman
from core.models import Config
from django.conf import settings
from django.db import connection, models, transaction
from django.db.models.functions import TruncDate
from django.utils import timezone
from stator.models import State, StateField, StateGraph, StatorModel


class HashtagStates(StateGraph):
    outdated = State(try_interval=300, force_initial=True)
    updated = State(externally_progressed=True)

    outdated.transitions_to(updated)
    updated.transitions_to(outdated)

    @classmethod
    def handle_outdated(cls, instance: "Hashtag"):
        """
        Computes the stats and other things for a Hashtag
        """
        from .post import Post

        today = timezone.now().date()
        # Use timezone-aware datetime ranges instead of __date / __year /
        # __month lookups so the FILTER predicates are plain timestamp
        # comparisons instead of per-row AT TIME ZONE / EXTRACT calls
        # (NEODB-SOCIAL-4QR -- single aggregate was hitting ~800ms on
        # popular hashtags because each candidate row had to be cast).
        today_start = timezone.make_aware(datetime(today.year, today.month, today.day))
        tomorrow_start = today_start + timedelta(days=1)
        month_start = today_start.replace(day=1)
        if today.month == 12:
            next_month_start = timezone.make_aware(datetime(today.year + 1, 1, 1))
        else:
            next_month_start = timezone.make_aware(
                datetime(today.year, today.month + 1, 1)
            )
        year_start = timezone.make_aware(datetime(today.year, 1, 1))
        next_year_start = timezone.make_aware(datetime(today.year + 1, 1, 1))
        # Collapse total / today / month / year into a single conditional
        # aggregate so we hit the DB once instead of four times per hashtag
        # transition (fired thousands of times daily by the stator loop).
        totals = (
            Post.objects.local_public()
            .tagged_with(instance)
            .aggregate(
                total=models.Count("id"),
                total_today=models.Count(
                    "id",
                    filter=models.Q(
                        created__gte=today_start, created__lt=tomorrow_start
                    ),
                ),
                total_month=models.Count(
                    "id",
                    filter=models.Q(
                        created__gte=month_start, created__lt=next_month_start
                    ),
                ),
                total_year=models.Count(
                    "id",
                    filter=models.Q(
                        created__gte=year_start, created__lt=next_year_start
                    ),
                ),
            )
        )
        total = totals["total"]
        total_today = totals["total_today"]
        total_month = totals["total_month"]
        total_year = totals["total_year"]

        # Mastodon doesn't include today in history (let's do it anyway).
        # One GROUP BY per-day aggregate over the 8-day window replaces the
        # previous 8 separate filter().aggregate() calls.
        history_start = today - timedelta(days=7)
        history_rows = (
            Post.objects.not_hidden()
            .tagged_with(instance)
            .annotate(_day=TruncDate("published"))
            .filter(_day__gte=history_start, _day__lte=today)
            .values("_day")
            .annotate(
                total=models.Count("id"),
                num_authors=models.Count("author", distinct=True),
            )
        )
        by_day = {row["_day"]: row for row in history_rows}
        history = []
        for i in range(8):
            day = today - timedelta(days=i)
            data = by_day.get(day)
            history.append(
                {
                    "day": str(int(time.mktime(day.timetuple()))),
                    "uses": str(data["total"]) if data else "0",
                    "accounts": str(data["num_authors"]) if data else "0",
                }
            )

        instance.stats = {
            "total": total,
            today.isoformat(): total_today,
            today.strftime("%Y-%m"): total_month,
            today.strftime("%Y"): total_year,
            "history": history,
        }
        instance.stats_updated = timezone.now()
        instance.save()

        return cls.updated


class HashtagQuerySet(models.QuerySet):
    def public(self):
        public_q = models.Q(public=True)
        if Config.system.hashtag_unreviewed_are_public:
            public_q |= models.Q(public__isnull=True)
        return self.filter(public_q)

    def hashtag_or_alias(self, hashtag: str):
        return self.filter(
            models.Q(hashtag=hashtag) | models.Q(aliases__contains=hashtag)
        )


class HashtagManager(models.Manager):
    def get_queryset(self):
        return HashtagQuerySet(self.model, using=self._db)

    def public(self):
        return self.get_queryset().public()

    def hashtag_or_alias(self, hashtag: str):
        return self.get_queryset().hashtag_or_alias(hashtag)


class Hashtag(StatorModel):
    MAXIMUM_LENGTH = 100

    # Normalized hashtag without the '#'
    hashtag = models.SlugField(primary_key=True, max_length=100)

    # Friendly display override
    name_override = models.CharField(max_length=100, null=True, blank=True)

    # Should this be shown in the public UI?
    public = models.BooleanField(null=True)

    # State of this Hashtag
    state = StateField(HashtagStates)

    # Metrics for this Hashtag
    stats = models.JSONField(null=True, blank=True)
    # Timestamp of last time the stats were updated
    stats_updated = models.DateTimeField(null=True, blank=True)

    # List of other hashtags that are considered similar
    aliases = models.JSONField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    objects = HashtagManager()

    class urls(urlman.Urls):
        view = "/tags/{self.hashtag}/"
        follow = "/tags/{self.hashtag}/follow/"
        unfollow = "/tags/{self.hashtag}/unfollow/"
        admin = "/admin/hashtags/"
        admin_edit = "{admin}{self.hashtag}/"
        admin_enable = "{admin_edit}enable/"
        admin_disable = "{admin_edit}disable/"
        timeline = "/tags/{self.hashtag}/"

    hashtag_regex = re.compile(r"\B#([a-zA-Z0-9(_)]+\b)(?!;)")

    def save(self, *args, **kwargs):
        self.hashtag = self.hashtag.lstrip("#")
        if self.name_override:
            self.name_override = self.name_override.lstrip("#")
        return super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.name_override or self.hashtag

    @property
    def needs_update(self):
        if self.stats_updated is None:
            return True
        return timezone.now() - self.stats_updated > timedelta(hours=1)

    def __str__(self):
        return self.display_name

    def usage_months(self, num: int = 12) -> dict[date, int]:
        """
        Return the most recent num months of stats
        """
        if not self.stats:
            return {}
        results = {}
        for key, val in self.stats.items():
            parts = key.split("-")
            if len(parts) == 2:
                year = int(parts[0])
                month = int(parts[1])
                results[date(year, month, 1)] = val
        return dict(sorted(results.items(), reverse=True)[:num])

    def usage_days(self, num: int = 7) -> dict[date, int]:
        """
        Return the most recent num days of stats
        """
        if not self.stats:
            return {}
        results = {}
        for key, val in self.stats.items():
            parts = key.split("-")
            if len(parts) == 3:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                results[date(year, month, day)] = val
        return dict(sorted(results.items(), reverse=True)[:num])

    @classmethod
    def popular(cls, days=7, limit=10, offset=None) -> list["Hashtag"]:
        sql = """
            SELECT jsonb_array_elements_text(hashtags) AS tag, count(id) AS uses
            FROM activities_post
            WHERE state NOT IN ('deleted', 'deleted_fanned_out') AND visibility IN (0,1,4) AND published >= %s
            GROUP BY tag
            ORDER BY uses DESC
            LIMIT %s
            OFFSET %s
        """
        since = timezone.now().date() - timedelta(days=days)
        if offset is None:
            offset = 0
        with connection.cursor() as cur:
            cur.execute(sql, (since, limit, offset))
            names = [r[0] for r in cur.fetchall()]
        # Grab all the popular tags at once.
        tags = {t.hashtag: t for t in cls.objects.filter(hashtag__in=names)}
        # Make sure we return the list in the original order of usage count.
        return [tags[name] for name in names if name in tags]

    @classmethod
    def ensure_hashtag(cls, name, update=None):
        """
        Properly strips/trims/lowercases the hashtag name, and makes sure a Hashtag
        object exists in the database, and returns it.
        """
        name = name.strip().lstrip("#").lower()[: Hashtag.MAXIMUM_LENGTH]
        hashtag, created = cls.objects.get_or_create(hashtag=name)
        if created or update or hashtag.needs_update:
            hashtag.transition_perform(HashtagStates.outdated)
        return hashtag

    @classmethod
    def handle_add_ap(cls, data):
        """
        Handles an incoming Add activity - sent when someone features a Hashtag.

        {
            "type": "Add",
            "actor": "https://hachyderm.io/users/dcw",
            "object": {
                "href": "https://hachyderm.io/@dcw/tagged/incarnator",
                "name": "#incarnator",
                "type": "Hashtag",
            },
            "target": "https://hachyderm.io/users/dcw/collections/featured",
        }
        """

        from users.models import Identity

        target = data.get("target", None)
        if not target:
            return

        with transaction.atomic():
            identity = Identity.by_actor_uri(data["actor"], create=True)
            # Featured tags target the featured collection URI, same as pinned posts.
            if identity.featured_collection_uri != target:
                return

            tag = Hashtag.ensure_hashtag(data["object"]["name"])
            return identity.hashtag_features.get_or_create(hashtag=tag)[0]

    @classmethod
    def handle_remove_ap(cls, data):
        """
        Handles an incoming Remove activity - sent when someone unfeatures a Hashtag.

        {
            "type": "Remove",
            "actor": "https://hachyderm.io/users/dcw",
            "object": {
                "href": "https://hachyderm.io/@dcw/tagged/netneutrality",
                "name": "#netneutrality",
                "type": "Hashtag",
            },
            "target": "https://hachyderm.io/users/dcw/collections/featured",
        }
        """

        from users.models import Identity

        target = data.get("target", None)
        if not target:
            return

        with transaction.atomic():
            identity = Identity.by_actor_uri(data["actor"], create=True)
            # Featured tags target the featured collection URI, same as pinned posts.
            if identity.featured_collection_uri != target:
                return

            tag = Hashtag.ensure_hashtag(data["object"]["name"])
            identity.hashtag_features.filter(hashtag=tag).delete()

    def to_ap(self, domain=None):
        hostname = domain.uri_domain if domain else settings.MAIN_DOMAIN
        return {
            "type": "Hashtag",
            "href": f"https://{hostname}/tags/{self.hashtag}/",
            "name": "#" + self.hashtag,
        }

    def to_add_ap(self, identity):
        """
        Returns the AP JSON to add a featured tag to the given identity.
        """
        return {
            "id": identity.actor_uri + "collections/featured/#add/" + self.hashtag,
            "type": "Add",
            "actor": identity.actor_uri,
            "target": identity.actor_uri + "collections/featured/",
            "object": self.to_ap(domain=identity.domain),
        }

    def to_remove_ap(self, identity):
        """
        Returns the AP JSON to remove a featured tag from the given identity.
        """
        return {
            "id": identity.actor_uri + "collections/featured/#remove/" + self.hashtag,
            "type": "Remove",
            "actor": identity.actor_uri,
            "target": identity.actor_uri + "collections/featured/",
            "object": self.to_ap(domain=identity.domain),
        }

    def to_mastodon_json(self, following: bool | None = None, domain=None):
        hostname = domain.uri_domain if domain else settings.MAIN_DOMAIN
        value = {
            "name": self.hashtag,
            "url": f"https://{hostname}/tags/{self.hashtag}/",
            "history": (self.stats or {}).get("history", []),
        }

        if following is not None:
            value["following"] = following

        return value
