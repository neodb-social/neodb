import base64
import calendar
import datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F
from django.db.models.functions import ExtractMonth
from django.http import HttpRequest, HttpResponseRedirect
from django.http.response import HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views.generic.base import TemplateView

from catalog.models import (
    AvailableItemCategory,
    ItemCategory,
    PodcastEpisode,
    item_content_types,
)
from journal.models import Comment, ShelfType
from mastodon.api import boost_toot_later, get_toot_visibility, post_toot_later
from takahe.utils import Takahe
from users.models import User

_type_emoji = {
    "movie": "🎬",
    "music": "💿",
    "album": "💿",
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
    return {v: _type_emoji.get(k.__name__.lower(), k.__name__) for k, v in cts.items()}


class WrappedView(LoginRequiredMixin, TemplateView):
    template_name = "wrapped.html"

    def get_context_data(self, **kwargs):
        user: User = self.request.user  # type: ignore
        target = user.identity
        year = int(kwargs.get("year", 0))
        if year < 1970 or year > 2100:
            year = datetime.date.today().year
        context = super().get_context_data(**kwargs)
        context["identity"] = target
        context["year"] = year
        cnt = {}
        cats = []
        _item_types = _type_to_emoji()
        for cat in AvailableItemCategory:
            queryset = target.shelf_manager.get_latest_members(
                ShelfType.COMPLETE, ItemCategory(cat)
            ).filter(created_time__year=year)
            cnt[cat] = queryset.count()
            if cat.value == "podcast":
                pc = (
                    Comment.objects.filter(
                        owner=target,
                        item__polymorphic_ctype_id=item_content_types()[PodcastEpisode],
                    )
                    .values("item__podcastepisode__program_id")
                    .distinct()
                    .count()
                )
                cnt[cat] += pc
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
            data[m - 1][_item_types[ct]] = data[m - 1].get(_item_types[ct], 0) + cnt
        podcast_by_month = list(
            Comment.objects.filter(
                owner=target,
                item__polymorphic_ctype_id=item_content_types()[PodcastEpisode],
            )
            .filter(created_time__year=year)
            .annotate(month=ExtractMonth("created_time"))
            .values("item__podcastepisode__program_id", "month")
            .distinct()
            .annotate(total=Count("month", distinct=True))
            .values_list("month", "total")
        )
        for m, cnt in podcast_by_month:
            data[m - 1]["🎙️"] = data[m - 1].get("🎙️", 0) + cnt
        context["data"] = data
        return context


class WrappedShareView(LoginRequiredMixin, TemplateView):
    template_name = "wrapped_share.html"

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        img = base64.b64decode(request.POST.get("img", ""))
        comment = request.POST.get("comment", "")
        visibility = int(request.POST.get("visibility", 0))
        user: User = request.user  # type: ignore
        identity = user.identity  # type: ignore
        media = Takahe.upload_image(
            identity.pk, "year.png", img, "image/png", "NeoDB Yearly Summary"
        )
        post = Takahe.post(
            identity.pk,
            "",
            comment,
            Takahe.visibility_n2t(visibility, user.preference.post_public_mode),
            attachments=[media],
        )
        classic_repost = user.preference.mastodon_repost_mode == 1
        if classic_repost:
            post_toot_later(
                user,
                comment,
                get_toot_visibility(visibility, user),
                img=img,
                img_name="year.png",
                img_type="image/png",
            )
        elif post:
            boost_toot_later(user, post.url)
        messages.add_message(request, messages.INFO, _("Summary posted to timeline."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
