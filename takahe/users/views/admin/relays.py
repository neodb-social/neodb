from django.core.exceptions import BadRequest
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.generic import ListView

from users.decorators import admin_required
from users.models import Relay


@method_decorator(admin_required, name="dispatch")
class RelaysRoot(ListView):
    template_name = "admin/relays.html"
    paginate_by = 30

    def get(self, request, *args, **kwargs):
        self.extra_context = {
            "section": "relays",
        }
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Relay.objects.all().order_by("-id")

    def post(self, request, *args, **kwargs):
        if "subscribe" in request.GET:
            Relay.subscribe(request.POST.get("inbox_uri"))
        elif "unsubscribe" in request.GET:
            self._posted_relay(request).unsubscribe()
        elif "remove" in request.GET:
            self._posted_relay(request).force_unsubscribe()
        return redirect("admin_relays")

    def _posted_relay(self, request: HttpRequest) -> Relay:
        try:
            pk = int(request.POST.get("id", ""))
        except ValueError:
            raise BadRequest("Invalid relay id")
        return get_object_or_404(Relay, pk=pk)
