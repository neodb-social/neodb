import json
from collections import Counter

from django.conf import settings

from catalog.dedup import (
    duplicate_primary_ids,
    merge_csv,
    read_csv,
    report,
    undo_entry,
)
from common.management.base import CommandError, SiteCommand

_HELP_TEXT = """
report:  write candidate duplicate pairs to a CSV for review (use -o FILE)
merge:   merge the CSV rows whose approve column is y, swap or recast
         (dry run unless --commit; use --tier to accept only some tiers)
undo:    revert the merges in an undo log, newest first
"""


class Command(SiteCommand):
    help = "find catalog items that share an external id, and merge approved pairs"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True, help=_HELP_TEXT)
        p = sub.add_parser("report", help="write candidate pairs to a CSV")
        p.add_argument("--output", "-o", required=True, help="CSV file to write")
        p.add_argument(
            "--primary-limit",
            type=int,
            default=50,
            help="items sharing a primary id to list (default: 50, 0 for all)",
        )
        p = sub.add_parser("merge", help="merge approved rows of a report CSV")
        p.add_argument("--csv", required=True, help="reviewed report CSV")
        p.add_argument(
            "--tier",
            action="append",
            help="only merge rows of this tier (repeat or comma-separate)",
        )
        p.add_argument("--commit", action="store_true", help="merge; default dry run")
        p.add_argument(
            "--log",
            help="undo log to append to (JSONL); required with --commit",
        )
        p = sub.add_parser("undo", help="revert the merges in an undo log")
        p.add_argument("--log", required=True, help="undo log written by merge")

    def handle(self, action, **options):
        match action:
            case "report":
                self.report(options["output"], options["primary_limit"])
            case "merge":
                tiers = {
                    t.strip()
                    for v in options["tier"] or []
                    for t in v.split(",")
                    if t.strip()
                }
                self.merge(options["csv"], tiers, options["commit"], options["log"])
            case "undo":
                self.undo(options["log"])

    def report(self, output: str, primary_limit: int):
        with open(output, "w", newline="", encoding="utf-8") as f:
            counts = report(f, settings.SITE_INFO["site_url"], self.stdout)
        for tier in sorted(counts):
            self.stdout.write(f"tier {tier}: {counts[tier]}")
        self.stdout.write(self.style.SUCCESS(f"{sum(counts.values())} pairs: {output}"))
        dups = duplicate_primary_ids(primary_limit or None)
        self.stdout.write(f"{len(dups)} primary ids shared by live items:")
        for model, t, v, ids in dups:
            self.stdout.write(f"  {model} {t}:{v} {ids}")

    def merge(self, path: str, tiers: set[str], commit: bool, log: str | None):
        if commit and not log:
            raise CommandError("--log is required with --commit")
        stats = Counter()
        with open(path, newline="", encoding="utf-8") as f:
            log_file = open(log, "a", encoding="utf-8") if commit and log else None
            try:
                for r in merge_csv(read_csv(f), commit, tiers or None, log_file):
                    stats[r.status] += 1
                    self.stdout.write(f"{r.status}: {r.source_id} -> {r.target_id}")
            finally:
                if log_file:
                    log_file.close()
        for status, n in stats.most_common():
            self.stdout.write(f"{status}: {n}")
        if not commit:
            self.stdout.write(self.style.WARNING("dry run; use --commit to merge"))

    def undo(self, path: str):
        with open(path, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        stats = Counter()
        for entry in reversed(entries):
            status = undo_entry(entry)
            stats[status] += 1
            self.stdout.write(f"{status}: {entry['source']} <- {entry['target']}")
        for status, n in stats.most_common():
            self.stdout.write(f"{status}: {n}")
