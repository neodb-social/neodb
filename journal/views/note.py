from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from catalog.models import Item
from common.forms import NeoModelForm
from common.utils import AuthedHttpRequest, get_uuid_or_404

from ..models import Note
from ..models.common import VisibilityType


class NoteForm(NeoModelForm):
    # _progress_choices = [
    #     ("", _("Progress Type (optional)"))
    # ] + Note.ProgressType.choices
    # progress_type = forms.ChoiceField(choices=_progress_choices, required=False)
    visibility = forms.ChoiceField(
        widget=forms.RadioSelect(), choices=VisibilityType.choices, initial=0
    )
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost to timeline"), initial=True, required=False
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

    def __init__(self, *args, **kwargs):
        item = kwargs.pop("item")
        super().__init__(*args, **kwargs)
        # allow submit empty content for existing note, and we'll delete it
        if self.instance.id:
            self.fields["content"].required = False
        # get the corresponding progress types for the item
        types = Note.get_progress_types_by_item(item)
        pt = self.instance.progress_type
        if pt and pt not in types:
            try:
                types.append(Note.ProgressType(pt))
            except ValueError:
                pass
        choices = [("", _("Progress Type (optional)"))] + [(x, x.label) for x in types]
        self.fields["progress_type"].choices = choices  # type: ignore


@login_required
@require_http_methods(["GET", "POST"])
def note_edit(request: AuthedHttpRequest, item_uuid: str, note_uuid: str = ""):
    item = get_object_or_404(Item, uid=get_uuid_or_404(item_uuid))
    owner = request.user.identity
    note_uuid = request.POST.get("uuid", note_uuid)
    note = None
    if note_uuid:
        note = get_object_or_404(
            Note, owner=owner, item=item, uid=get_uuid_or_404(note_uuid)
        )
    form = NoteForm(
        request.POST or None, item=item, instance=note, initial={"uuid": note_uuid}
    )
    form.instance.owner = owner
    form.instance.item = item
    if request.method == "GET":
        return render(
            request,
            "note.html",
            {
                "item": item,
                "note": note,
                "form": form,
            },
        )
    if not form.data["content"]:
        if not note:
            raise Http404(_("Content not found"))
        note.delete()
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    if not form.is_valid():
        raise BadRequest(_("Invalid form data"))
    form.instance.crosspost_when_save = form.cleaned_data["share_to_mastodon"]
    note = form.save()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
