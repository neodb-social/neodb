from common.management.base import SiteCommand

_HELP_TEXT = """
similarity:    rebuild ItemSimilarity (full)
users:         refresh UserRecommendation for active users
all:           similarity + users

Runs unconditionally, independent of the `enable_recommendations` site flag,
so operators can pre-build data and inspect it before enabling the surfaces.
The serving layer remains gated by the flag and user preference until enabled.
"""


class Command(SiteCommand):
    help = "Run recommendation batch jobs immediately (bypasses site flag for dry-run)."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=["similarity", "users", "all"],
            help=_HELP_TEXT,
        )

    def handle(self, *args, **options):
        from catalog.jobs.recommendation import (
            BuildItemSimilarity,
            BuildUserRecommendations,
        )

        action = options["action"]
        if action in ("similarity", "all"):
            self.stdout.write("Building item similarity ...")
            BuildItemSimilarity().run()
        if action in ("users", "all"):
            self.stdout.write("Refreshing user recommendations ...")
            BuildUserRecommendations().run()
        self.stdout.write(self.style.SUCCESS("Done."))
