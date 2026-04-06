import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from tqdm import tqdm

from common.management.base import SiteCommand
from journal.models import Collection, Review

_RE_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _migrate_image(src: str, identity_id: int, created_year: str) -> str | None:
    """Migrate an old image path to the new upload/ location.

    Returns new path if migrated, None if no change needed.
    """
    from urllib.parse import urlparse

    from django.conf import settings

    parsed = urlparse(src)
    media_parsed = urlparse(settings.MEDIA_URL)
    site_domains = set(getattr(settings, "SITE_DOMAINS", [settings.SITE_DOMAIN]))
    media_host = media_parsed.hostname or ""
    media_path = media_parsed.path

    if parsed.scheme in ("http", "https") and parsed.netloc:
        src_host = parsed.hostname or ""
        is_our_server = src_host in site_domains
        is_media_host = media_host and src_host == media_host
        if not (is_our_server or is_media_host):
            return None  # external URL, skip
        src = parsed.path
    elif not src.startswith("/"):
        return None

    if not src.startswith(media_path):
        return None
    rel_path = src[len(media_path) :]

    # Already migrated
    if rel_path.startswith("upload/"):
        return None

    # Check if source file exists
    if not default_storage.exists(rel_path):
        return None

    # Generate new path
    ext = rel_path.rsplit(".", 1)[-1] if "." in rel_path else "jpg"
    new_rel = f"upload/{identity_id}/{created_year}/{uuid.uuid4()}.{ext}"

    # Copy file to new location
    with default_storage.open(rel_path) as f:
        default_storage.save(new_rel, ContentFile(f.read()))

    return settings.MEDIA_URL + new_rel


def _process_content(text: str, identity_id: int, created_year: str) -> str | None:
    """Process markdown text, migrating image paths. Returns new text or None if unchanged."""
    changed = False

    def _replace(m: re.Match[str]) -> str:
        nonlocal changed
        alt = m.group(1)
        src = m.group(2).strip()
        new_src = _migrate_image(src, identity_id, created_year)
        if new_src is not None:
            changed = True
            return f"![{alt}]({new_src})"
        return m.group(0)

    result = _RE_MD_IMAGE.sub(_replace, text)
    return result if changed else None


class Command(SiteCommand):
    help = "Migrate review/collection images to upload/<identity_id>/<year>/ path"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write("Dry run mode - no changes will be made\n")

        review_count = 0
        collection_count = 0

        reviews = Review.objects.filter(body__contains="![").select_related("owner")
        self.stdout.write(f"Scanning {reviews.count()} reviews with images...\n")
        for review in tqdm(reviews, desc="Reviews"):
            year = review.created_time.strftime("%Y")
            new_body = _process_content(review.body, review.owner_id, year)
            if new_body is not None:
                review_count += 1
                if dry_run:
                    self.stdout.write(f"  Review {review.pk}: would migrate images\n")
                else:
                    Review.objects.filter(pk=review.pk).update(body=new_body)

        collections = Collection.objects.filter(brief__contains="![").select_related(
            "owner"
        )
        self.stdout.write(
            f"Scanning {collections.count()} collections with images...\n"
        )
        for collection in tqdm(collections, desc="Collections"):
            year = collection.created_time.strftime("%Y")
            new_brief = _process_content(collection.brief, collection.owner_id, year)
            if new_brief is not None:
                collection_count += 1
                if dry_run:
                    self.stdout.write(
                        f"  Collection {collection.pk}: would migrate images\n"
                    )
                else:
                    Collection.objects.filter(pk=collection.pk).update(brief=new_brief)

        action = "Would migrate" if dry_run else "Migrated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} images in {review_count} reviews and {collection_count} collections\n"
            )
        )
