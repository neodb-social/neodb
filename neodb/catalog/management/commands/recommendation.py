import json
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

from django.core.management.base import CommandParser
from django.utils import timezone

from common.management.base import CommandError, SiteCommand

if TYPE_CHECKING:
    from catalog.evaluation import EvaluationResult

_HELP_TEXT = """
similarity:    rebuild ItemSimilarity (full)
users:         refresh UserRecommendation for active users
all:           similarity + users
evaluate:      report how well the current settings predict what members
               marked in the last --holdout-days, writing nothing

Runs unconditionally, independent of the `enable_recommendations` site flag,
so operators can pre-build data and inspect it before enabling the surfaces.
The serving layer remains gated by the flag and user preference until enabled.

evaluate builds the whole item similarity in memory from the marks, ratings,
annotations, tags and collection entries before the cutoff, so it costs
about as much time and memory as the weekly similarity job. Seeds come from
before the cutoff too, and the seed-shelf marks after it are the truth.
Catalog data (credits, merges, deletions), tag titles and visibility,
discoverability and member preferences are read as of now. A mark moved to
another shelf after the cutoff counts as held out, not as a seed, and a
rating changed after the cutoff is seen with its new grade.
"""


class Command(SiteCommand):
    help = "Run recommendation batch jobs immediately (bypasses site flag for dry-run)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "action",
            choices=["similarity", "users", "all", "evaluate"],
            help=_HELP_TEXT,
        )
        parser.add_argument(
            "--holdout-days",
            type=int,
            default=30,
            help="evaluate: the cutoff is this many days ago",
        )
        parser.add_argument(
            "--users",
            type=int,
            default=200,
            help="evaluate: members to sample, 0 for every eligible member",
        )
        parser.add_argument(
            "--sample-seed",
            type=int,
            default=0,
            help="evaluate: random seed of the member sample",
        )
        parser.add_argument(
            "--min-seeds",
            type=int,
            default=5,
            help="evaluate: seed-shelf marks a member needs before the cutoff",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=None,
            help="evaluate: rows per member, default reco_user_top_n",
        )
        parser.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="evaluate: override a reco_* setting for this run only, repeatable",
        )
        parser.add_argument(
            "--json",
            metavar="PATH",
            help="evaluate: also write the result as JSON",
        )

    def handle(self, *args, **options) -> None:
        from catalog.jobs.recommendation import (
            BuildItemSimilarity,
            BuildUserRecommendations,
        )

        action = options["action"]
        if action == "evaluate":
            self._evaluate(options)
            return
        if action in ("similarity", "all"):
            self.stdout.write("Building item similarity ...")
            BuildItemSimilarity().run()
        if action in ("users", "all"):
            self.stdout.write("Refreshing user recommendations ...")
            BuildUserRecommendations().run()
        self.stdout.write(self.style.SUCCESS("Done."))

    def _evaluate(self, options: dict) -> None:
        from catalog.evaluation import evaluate, parse_overrides

        if options["holdout_days"] <= 0:
            raise CommandError("--holdout-days must be positive")
        if options["top_n"] is not None and options["top_n"] <= 0:
            raise CommandError("--top-n must be positive")
        try:
            overrides = parse_overrides(options["set"])
        except ValueError as e:
            raise CommandError(str(e)) from e
        cutoff = timezone.now() - timedelta(days=options["holdout_days"])
        self.stdout.write(
            f"Evaluating recommendations as of {cutoff:%Y-%m-%d %H:%M} ..."
        )
        try:
            result = evaluate(
                cutoff,
                users=options["users"],
                sample_seed=options["sample_seed"],
                min_seeds=options["min_seeds"],
                top_n=options["top_n"],
                overrides=overrides,
            )
        except ValueError as e:
            raise CommandError(str(e)) from e
        self._report(result)
        if options["json"]:
            with open(options["json"], "w") as f:
                json.dump(result.as_dict(), f, indent=2, default=str)
            self.stdout.write(f"Wrote {options['json']}")
        self.stdout.write(self.style.SUCCESS("Done."))

    def _report(self, result: "EvaluationResult") -> None:
        w = self.stdout.write
        w(f"Cutoff: {result.cutoff.isoformat()}")
        w(
            f"Members: {result.members_eligible} eligible, "
            f"{result.members_sampled} sampled, {result.members_evaluated} evaluated"
        )
        w("Settings:")
        for key, value in result.settings.items():
            mark = "  (override)" if key in result.overrides else ""
            w(f"  {key} = {value}{mark}")
        w("Timings:")
        for key, seconds in result.timings.items():
            w(f"  {key}: {seconds:.1f} s")

        reco, pop = result.recommendations, result.popularity
        rows: list[tuple[str, str, str]] = []
        for a, b in zip(reco.at_k, pop.at_k):
            rows.append((f"hit rate@{a.k}", f"{a.hit_rate:.4f}", f"{b.hit_rate:.4f}"))
            rows.append(
                (f"precision@{a.k}", f"{a.precision:.4f}", f"{b.precision:.4f}")
            )
            rows.append((f"recall@{a.k}", f"{a.recall:.4f}", f"{b.recall:.4f}"))
        rows += [
            (
                "members with a list",
                str(reco.members_with_list),
                str(pop.members_with_list),
            ),
            ("coverage", f"{reco.coverage:.4f}", f"{pop.coverage:.4f}"),
            (
                f"distinct items@{result.top_n}",
                str(reco.distinct_items),
                str(pop.distinct_items),
            ),
            (
                "mean list length",
                f"{reco.mean_list_length:.1f}",
                f"{pop.mean_list_length:.1f}",
            ),
        ]
        for label, attr, fmt in self._DIVERSITY:
            rows.append(
                (
                    label,
                    self._value(getattr(reco.diversity, attr), fmt),
                    self._value(getattr(pop.diversity, attr), fmt),
                )
            )
        self._table(("Metric", "Recommendations", "Popularity"), rows)

        w(
            f"By seed-shelf marks before the cutoff, hit rate@{result.top_n} "
            "and diversity of recommendations (popularity hit rate):"
        )
        bucket_rows = []
        for label, r in reco.by_bucket.items():
            p = pop.by_bucket.get(label)
            bucket_rows.append(
                (
                    label,
                    str(r.members),
                    f"{r.hit_rate:.4f}",
                    f"{p.hit_rate:.4f}" if p else "-",
                    *(
                        self._value(getattr(r.diversity, attr), fmt)
                        for _, attr, fmt in self._DIVERSITY
                    ),
                )
            )
        self._table(
            (
                "Marks",
                "Members",
                "Hit rate",
                "Popular",
                *(label for label, _, _ in self._DIVERSITY),
            ),
            bucket_rows,
        )

        w(
            f"Hit rate@{result.top_n} by category, among members whose list "
            "has that category:"
        )
        cat_rows = []
        for cat in sorted(reco.by_category.keys() | pop.by_category.keys()):
            r = reco.by_category.get(cat)
            p = pop.by_category.get(cat)
            cat_rows.append(
                (
                    cat,
                    f"{r.hit_rate:.4f} of {r.members}" if r else "-",
                    f"{p.hit_rate:.4f} of {p.members}" if p else "-",
                )
            )
        self._table(("Category", "Recommendations", "Popularity"), cat_rows)

    _DIVERSITY = (
        ("lead seeds", "lead_seeds", ".1f"),
        ("top seed share", "top_seed_share", ".2f"),
        ("duplicate rate", "duplicate_rate", ".4f"),
        ("max per creator", "max_per_creator", ".2f"),
        ("intra-list similarity", "intra_list_similarity", ".4f"),
    )

    @staticmethod
    def _value(value: float | None, fmt: str) -> str:
        return "-" if value is None else format(value, fmt)

    def _table(self, header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> None:
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            self.stdout.write(
                "  ".join(
                    cell.ljust(width) if i == 0 else cell.rjust(width)
                    for i, (cell, width) in enumerate(zip(row, widths))
                )
            )
