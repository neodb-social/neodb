from auditlog.context import set_actor
from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q
from django.utils.translation import gettext_lazy as t

from catalog.models import *
from journal.models import *
from users.middlewares import activate_language_for_user
from users.models import *
from users.models import APIdentity


class Command(BaseCommand):
    help = "Calculate Top 10"

    def add_arguments(self, parser):
        parser.add_argument("year", type=int, help="year to calculate")
        parser.add_argument("--save", default="", help="save as collection for user")
        parser.add_argument(
            "--top", default=10, type=int, help="save as collection for user"
        )

    def handle(self, year: int, top: int, save: str, **options):  # type: ignore
        collector = APIdentity.objects.get(username=save, local=True) if save else None
        if collector:
            activate_language_for_user(collector.user)
        types = [
            [Edition],
            [Movie],
            [TVShow, TVSeason],
            [Game],
            [Podcast],
            [Album],
            [Performance],
        ]
        mapping = item_content_types()
        for typ in types:
            cids = [mapping[t] for t in typ]
            title = f"{year}年标记最多的{t(typ[0].category.label)}"
            print(title)
            top10 = list(
                ShelfMember.objects.filter(
                    created_time__year=year, item__polymorphic_ctype__in=cids
                )
                .values("item")
                .annotate(c=Count("item"))
                .order_by("-c")[:top]
            )
            items = [(Item.objects.get(pk=i["item"]), i["c"]) for i in top10]
            _ = [print(c, i.display_title, i.absolute_url) for i, c in items]
            if collector:
                with set_actor(collector.user):
                    print(f"Saving to {collector}")
                    c, _ = Collection.objects.get_or_create(
                        owner=collector,
                        title=title,
                        brief="*根据用户标记数统计*",
                        defaults={"visibility": 2},
                    )
                    for i, cat in items:
                        c.append_item(i)

        # top10 = list(
        #     Comment.objects.filter(
        #         created_time__year=2023, item__polymorphic_ctype=mapping[PodcastEpisode]
        #     )
        #     .values(i=F("item__podcastepisode__program_id"))
        #     .annotate(c=Count("i"))
        #     .order_by("-c")[:15]
        # )
        # items = [Item.objects.get(pk=i["i"]) for i in top10]
        # _ = [print(i.title, i.absolute_url) for i in items]
