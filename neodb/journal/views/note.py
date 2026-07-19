from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item
from common.forms import NeoModelForm
from common.sentry import record_activity
from common.utils import AuthedHttpRequest, get_uuid_or_404

from ..models import Mark, Note, ShelfType
from ..models.common import VisibilityType


class NoteForm(NeoModelForm):
    mode = forms.ChoiceField(
        choices=(("note", _("Note")), ("progress", _("Progress"))),
        initial="note",
        widget=forms.RadioSelect(),
    )
    update_progress = forms.BooleanField(
        label=_("Update progress"), initial=False, required=False
    )
    visibility = forms.ChoiceField(
        widget=forms.RadioSelect(), choices=VisibilityType.choices, initial=0
    )
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost"), initial=False, required=False
    )
    uuid = forms.CharField(widget=forms.HiddenInput(), required=False)
    # content = forms.CharField(required=False, widget=forms.Textarea)

    class Meta:
        model = Note
        fields = [
            "id",
            "title",
            "content",
            "visibility",
            "progress_type",
            "progress_value",
            "sensitive",
        ]
        widgets = {
            "progress_value": forms.TextInput(
                attrs={"placeholder": _("Progress (optional)")}
            ),
            "content": forms.Textarea(attrs={"placeholder": _("Note Content")}),
            "title": forms.TextInput(
                attrs={"placeholder": _("Content Warning (optional)")}
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        item = kwargs.pop("item")
        mode = kwargs.pop("mode", "note")
        super().__init__(*args, **kwargs)
        # allow submit empty content for existing note, and we'll delete it
        if self.instance.id:
            self.fields["content"].required = False
        # get the corresponding progress types for the item
        types = Note.get_progress_types_by_item(item)
        pt = self.instance.progress_type
        if mode == "progress":
            self.fields["visibility"].required = False
            self.fields["content"].required = False
        if pt and pt not in types:
            try:
                types.append(Note.ProgressType(pt))
            except ValueError:
                pass
        choices = [("", _("Progress Type (optional)"))] + [(x, x.label) for x in types]
        self.fields["progress_type"].choices = choices  # type: ignore


def _return_url(request: AuthedHttpRequest) -> str:
    referer = request.META.get("HTTP_REFERER") or ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=set(settings.SITE_DOMAINS),
        require_https=settings.SSL_ONLY,
    ):
        return "/"
    return referer


@login_required
@require_http_methods(["GET", "POST"])
def note_edit(
    request: AuthedHttpRequest, item_uuid: str, note_uuid: str = ""
) -> HttpResponse:
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    owner = request.user.identity
    note_uuid = request.POST.get("uuid", note_uuid)
    note = None
    if note_uuid:
        note = get_object_or_404(
            Note, owner=owner, item=item, uid=get_uuid_or_404(note_uuid)
        )

    mark = Mark(owner, item)
    can_update_progress = (
        item.class_name == "edition" and mark.shelf_type == ShelfType.PROGRESS
    )
    requested_mode = request.POST.get("mode") or request.GET.get("mode") or "note"
    if note:
        mode = "note"
    elif requested_mode == "progress" and can_update_progress:
        mode = "progress"
    elif requested_mode == "progress" and request.method == "POST":
        raise BadRequest(_("Only in-progress books can have reading progress."))
    else:
        mode = "note"

    initial = {"uuid": note_uuid, "mode": mode, "share_to_mastodon": False}
    if not note:
        initial.update(
            {
                "progress_type": mark.progress_type,
                "progress_value": mark.progress_value,
            }
        )
    form = NoteForm(
        request.POST or None,
        item=item,
        mode=mode,
        instance=note,
        initial=initial,
    )
    form.instance.owner = owner
    form.instance.item = item
    context = {
        "item": item,
        "note": note,
        "form": form,
        "mode": mode,
        "can_update_progress": can_update_progress,
        "has_current_progress": bool(mark.progress_value),
    }

    if request.method == "GET":
        return render(request, "note.html", context)

    if mode == "progress":
        try:
            if request.POST.get("clear_progress"):
                mark.set_progress(None, None)
            else:
                if not form.is_valid():
                    return render(request, "note.html", context, status=400)
                mark.set_progress(
                    form.cleaned_data["progress_type"],
                    form.cleaned_data["progress_value"],
                )
        except ValueError as error:
            form.add_error("progress_value", str(error))
            return render(request, "note.html", context, status=400)
        record_activity("progress", "web")
        return HttpResponseRedirect(_return_url(request))

    if not form.data.get("content"):
        if not note:
            raise Http404(_("Content not found"))
        note.delete()
        return HttpResponseRedirect(_return_url(request))

    if not form.is_valid():
        return render(request, "note.html", context, status=400)
    if form.cleaned_data["update_progress"] and not can_update_progress:
        form.add_error(
            "update_progress", _("Only in-progress books can have reading progress.")
        )
        return render(request, "note.html", context, status=400)

    try:
        with transaction.atomic():
            form.instance.crosspost_when_save = form.cleaned_data["share_to_mastodon"]
            note = form.save()
            if form.cleaned_data["update_progress"]:
                mark.set_progress(
                    form.cleaned_data["progress_type"],
                    form.cleaned_data["progress_value"],
                )
    except ValueError as error:
        form.add_error("progress_value", str(error))
        return render(request, "note.html", context, status=400)

    record_activity("note", "web")
    return HttpResponseRedirect(_return_url(request))
