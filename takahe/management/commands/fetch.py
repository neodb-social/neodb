from time import sleep

import httpx
from django.core.management.base import BaseCommand, CommandError

from catalog.sites.fedi import FediverseInstance
from takahe.models import Identity, InboxMessage, Post

actor_types = ["person", "service", "application", "group", "organization"]
post_types = ["note", "article", "post", "question", "event", "video", "audio", "image"]


class Command(BaseCommand):
    help = "Fetch a post from a URL"

    def add_arguments(self, parser):
        parser.add_argument(
            "url",
            type=str,
            help="URL of the post to fetch",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Timeout in seconds for fetching operation (default: 30)",
        )

    def handle(self, *args, **options):
        url = options["url"]
        timeout = options["timeout"]
        self.stdout.write(f"Fetching post from URL: {url}")
        try:
            headers = {
                "Accept": "application/json,application/activity+json,application/ld+json"
            }
            response = httpx.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            self.stdout.write(f"Content-Type: {content_type}")
            if any(
                content_type.endswith(json_type)
                for json_type in ["json; charset=utf-8", "json"]
            ):
                j = response.json()
                typ = j.get("type", "").lower()
                uri = j.get("id", "")
                if not typ or not uri:
                    self.stdout.write(self.style.WARNING("Unknown object id/type"))
                elif typ in actor_types:
                    InboxMessage.create_internal({"type": "searchurl", "url": url})
                    self.stdout.write("Fetching Takahe identity", ending="")
                    tries = timeout
                    while tries > 0:
                        self.stdout.write(".", ending="")
                        tries -= 1
                        i = Identity.objects.filter(actor_uri=uri).first()
                        if i:
                            self.stdout.write(
                                self.style.SUCCESS(f"\nIdentity fetched: @{i.handle}")
                            )
                            break
                        sleep(1)
                        if tries == 0:
                            self.stdout.write(self.style.ERROR("timeout"))
                elif typ in post_types:
                    InboxMessage.create_internal({"type": "searchurl", "url": url})
                    self.stdout.write("Fetching Takahe post", ending="")
                    tries = timeout
                    while tries > 0:
                        self.stdout.write(".", ending="")
                        tries -= 1
                        p = Post.objects.filter(object_uri=uri).first()
                        if p:
                            self.stdout.write(
                                self.style.SUCCESS(f"\nPost fetched: {p}\n{p.content}")
                            )
                            break
                        sleep(1)
                        if tries == 0:
                            self.stdout.write(self.style.ERROR("timeout"))
                else:
                    s = FediverseInstance(url=url)
                    r = s.get_resource_ready()
                    if r:
                        self.stdout.write(
                            self.style.SUCCESS(f"NeoDB resource is ready: {r.metadata}")
                        )
            else:
                self.stdout.write(
                    self.style.WARNING(f"Content type is not JSON: {content_type}")
                )
        except httpx.RequestError as e:
            raise CommandError(f"Request error: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise CommandError(f"HTTP error: {e.response.status_code} {str(e)}")
