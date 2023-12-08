from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F
from django.db.models.functions import ExtractMonth
from django.views.generic.base import TemplateView

from catalog.models import AvailableItemCategory, ItemCategory
from journal.models import ShelfType
from users.models import User


class WrappedView(LoginRequiredMixin, TemplateView):
    template_name = "wrapped.html"

    def get_context_data(self, **kwargs):
        user: User = self.request.user  # type: ignore
        target = user.identity
        year = kwargs.get("year")
        context = super().get_context_data(**kwargs)
        context["identity"] = target
        cnt = {}
        for cat in AvailableItemCategory:
            queryset = target.shelf_manager.get_latest_members(
                ShelfType.COMPLETE, ItemCategory(cat)
            ).filter(created_time__year=year)
            cnt[cat] = queryset.count()
        context["by_cat"] = {k: cnt[k] for k in cnt if cnt[k] > 0}
        monthly = list(
            target.shelf_manager.get_latest_members(ShelfType.COMPLETE)
            .filter(created_time__year=year)
            .annotate(month=ExtractMonth("created_time"))
            .values("month")
            .annotate(total=Count("month"))
            .order_by("month")
            .values_list("month", "total")
        )
        monthly = {k: v for k, v in monthly}
        context["monthly"] = [monthly.get(k, 0) for k in range(1, 13)]
        # .annotate(cat=F("item__polymorphic_ctype_id")).values("cat")
        return context
