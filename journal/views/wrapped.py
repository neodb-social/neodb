import calendar

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F
from django.db.models.functions import ExtractMonth
from django.views.generic.base import TemplateView

from catalog.models import AvailableItemCategory, ItemCategory, item_content_types
from journal.models import ShelfType
from users.models import User

_type_emoji = {
    "movie": "🎬",
    "music": "💽",
    "game": "🎮",
    "tv": "📺",
    "tvshow": "📺",
    "tvseason": "📺",
    "book": "📚",
    "edition": "📚",
    "podcast": "🎙️",
    "performance": "🎭",
    "performanceproduction": "🎭",
}


def _type_to_emoji():
    cts = item_content_types()
    return {v: _type_emoji.get(k.__name__.lower(), "") for k, v in cts.items()}


_item_types = _type_to_emoji()


class WrappedView(LoginRequiredMixin, TemplateView):
    template_name = "wrapped.html"

    def get_context_data(self, **kwargs):
        user: User = self.request.user  # type: ignore
        target = user.identity
        year = kwargs.get("year")
        context = super().get_context_data(**kwargs)
        context["identity"] = target
        cnt = {}
        cats = []
        for cat in AvailableItemCategory:
            queryset = target.shelf_manager.get_latest_members(
                ShelfType.COMPLETE, ItemCategory(cat)
            ).filter(created_time__year=year)
            cnt[cat] = queryset.count()
            if cnt[cat] > 0:
                cats.append(f"{_type_emoji[cat.value]}x{cnt[cat]}")
        context["by_cat"] = "  ".join(cats)
        all = list(
            target.shelf_manager.get_latest_members(ShelfType.COMPLETE)
            .filter(created_time__year=year)
            .annotate(month=ExtractMonth("created_time"))
            .annotate(cat=F("item__polymorphic_ctype_id"))
            .values("month", "cat")
            .annotate(total=Count("month"))
            .order_by("month")
            .values_list("month", "cat", "total")
        )
        data = [{"Month": calendar.month_abbr[m]} for m in range(1, 13)]
        for m, ct, cnt in all:
            data[m - 1][_item_types[ct]] = cnt
        context["data"] = data
        return context
