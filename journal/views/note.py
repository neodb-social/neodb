from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest, ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
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
        label=_("Post to Fediverse"), initial=True, required=False
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
        if self.instance.progress_type and self.instance.progress_type not in types:
            types.append(self.instance.progress_type)
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
    if note:
        orig_visibility = note.visibility
    else:
        orig_visibility = None
    if not form.is_valid():
        raise BadRequest(_("Invalid form data"))
    note = form.save()
    delete_existing_post = (
        orig_visibility is not None and orig_visibility != note.visibility
    )
    note.sync_to_timeline(delete_existing=delete_existing_post)
    if form.cleaned_data["share_to_mastodon"]:
        note.sync_to_mastodon(delete_existing=delete_existing_post)
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
