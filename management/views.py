from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)
from django.views.generic.edit import ModelFormMixin

from .models import Announcement

# https://docs.djangoproject.com/en/3.1/topics/class-based-views/intro/
decorators = [login_required, user_passes_test(lambda u: u.is_superuser)]  # type:ignore


class AnnouncementDetailView(DetailView, ModelFormMixin):
    model = Announcement
    fields = ["content"]
    template_name = "management/detail.html"


class AnnouncementListView(ListView):
    model = Announcement
    # paginate_by = 1
    template_name = "management/list.html"

    def get_queryset(self):
        return Announcement.objects.all().order_by("-pk")


@method_decorator(decorators, name="dispatch")
class AnnouncementDeleteView(DeleteView):
    model = Announcement
    success_url = reverse_lazy("management:list")
    template_name = "management/delete.html"


@method_decorator(decorators, name="dispatch")
class AnnouncementCreateView(CreateView):
    model = Announcement
    fields = "__all__"
    template_name = "management/create_update.html"


@method_decorator(decorators, name="dispatch")
class AnnouncementUpdateView(UpdateView):
    model = Announcement
    fields = "__all__"
    template_name = "management/create_update.html"

    def form_valid(self, form):
        form.instance.edited_time = timezone.now()
        return super().form_valid(form)
