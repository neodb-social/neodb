import mimetypes
import os
import re
import tempfile
import zipfile

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from activities.models import Emoji


class Command(BaseCommand):
    help = "Import custom emoji from a zip file URL"

    def add_arguments(self, parser: "BaseCommand.ArgumentParser") -> None:
        parser.add_argument(
            "url",
            help="URL to a zip file containing emoji images",
        )
        parser.add_argument(
            "--category",
            default="",
            help="Category to assign to all imported emoji",
        )
        parser.add_argument(
            "--prefix",
            default="",
            help="Prefix to prepend to each shortcode (e.g. 'blob_')",
        )
        parser.add_argument(
            "--max-size",
            type=int,
            default=1024,
            help="Maximum file size in KB (default: 1024)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be imported without actually importing",
        )

    def handle(self, url: str, **options) -> None:
        category = options["category"]
        prefix = options["prefix"]
        max_size_kb = options["max_size"]
        dry_run = options["dry_run"]

        self.stdout.write(f"Downloading {url} ...")
        try:
            response = httpx.get(
                url,
                follow_redirects=True,
                timeout=60,
                headers={"User-Agent": settings.TAKAHE_USER_AGENT},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise CommandError(f"Failed to download: {e}")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "emoji.zip")
            with open(zip_path, "wb") as f:
                f.write(response.content)

            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmpdir)
            except zipfile.BadZipFile:
                raise CommandError("Downloaded file is not a valid zip archive")

            image_extensions = {".png", ".gif", ".webp", ".jpg", ".jpeg", ".svg"}
            image_files: list[tuple[str, str]] = []
            for dirpath, _dirnames, filenames in os.walk(tmpdir):
                for filename in sorted(filenames):
                    name, ext = os.path.splitext(filename)
                    if ext.lower() in image_extensions:
                        filepath = os.path.join(dirpath, filename)
                        image_files.append((filepath, filename))

            if not image_files:
                raise CommandError("No image files found in the zip archive")

            self.stdout.write(f"Found {len(image_files)} image(s)")

            existing = set(
                Emoji.objects.filter(local=True).values_list("shortcode", flat=True)
            )

            created = 0
            skipped_dup = 0
            skipped_bad = 0

            for filepath, filename in image_files:
                name, ext = os.path.splitext(filename)
                shortcode = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
                if not shortcode:
                    self.stderr.write(f"  skip {filename}: cannot derive shortcode")
                    skipped_bad += 1
                    continue

                shortcode = prefix + shortcode

                if not re.match(r"^[a-z0-9_]+$", shortcode):
                    self.stderr.write(
                        f"  skip {filename}: invalid shortcode '{shortcode}'"
                    )
                    skipped_bad += 1
                    continue

                if len(shortcode) > 100:
                    self.stderr.write(f"  skip {filename}: shortcode too long")
                    skipped_bad += 1
                    continue

                if shortcode in existing:
                    self.stderr.write(f"  skip :{shortcode}: (already exists)")
                    skipped_dup += 1
                    continue

                mimetype, _ = mimetypes.guess_type(filename)
                if not mimetype or not mimetype.startswith("image/"):
                    self.stderr.write(f"  skip {filename}: unknown mimetype")
                    skipped_bad += 1
                    continue

                file_size = os.path.getsize(filepath)
                max_size = max_size_kb * 1024
                if file_size > max_size:
                    self.stderr.write(
                        f"  skip {filename}: too large ({file_size // 1024}KB > {max_size_kb}KB)"
                    )
                    skipped_bad += 1
                    continue

                if dry_run:
                    self.stdout.write(f"  would import :{shortcode}: from {filename}")
                    created += 1
                    continue

                with open(filepath, "rb") as f:
                    file_content = ContentFile(f.read(), name=filename)

                Emoji.objects.create(
                    shortcode=shortcode,
                    file=file_content,
                    mimetype=mimetype,
                    local=True,
                    public=True,
                    category=category or None,
                )
                existing.add(shortcode)
                created += 1
                self.stdout.write(f"  imported :{shortcode}:")

            action = "Would import" if dry_run else "Imported"
            self.stdout.write(
                f"\n{action} {created}, skipped {skipped_dup} duplicates, skipped {skipped_bad} invalid"
            )
