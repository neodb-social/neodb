import json

from django.core.management.base import BaseCommand

from catalog.models import Item
from common.utils import get_uuid_or_404
from journal.models import *
from takahe.utils import *
from users.models import APIdentity


# TODO make this available in UI
class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument("--export", default="", action="store")
        parser.add_argument("--import", default="", action="store")

    def handle(self, *args, **options):
        self.verbose = options["verbose"]

        if options["import"]:
            self.process_import(options["import"])

        if options["export"]:
            self.process_export(options["export"])

        self.stderr.write(self.style.SUCCESS("Done."))

    def process_export(self, collection_uuid):
        try:
            collection = Collection.objects.get(uid=get_uuid_or_404(collection_uuid))
        except Collection.DoesNotExist:
            self.stderr.write(self.style.ERROR("Collection not found."))
            return
        self.stderr.write(self.style.SUCCESS(f"Exporting {collection}"))
        data = {
            "title": collection.title,
            "brief": collection.brief,
            "items": [],
        }
        for member in collection.ordered_members.all():
            data["items"].append(
                {
                    "title": member.item.title,
                    "url": member.item.absolute_url,
                    "note": member.note,  # type:ignore
                }
            )
        print(json.dumps(data, indent=2))

    def process_import(self, username):
        owner = APIdentity.objects.get(username=username, local=True)
        data = json.load(sys.stdin)
        collection = Collection.objects.create(
            owner=owner,
            title=data["title"],
            brief=data["brief"],
        )
        for item in data["items"]:
            i = Item.get_by_url(item["url"])
            collection.append_item(i, note=item["note"])
            self.stderr.write(self.style.SUCCESS(f"Added {i}"))
