from django import forms
from django.utils.translation import gettext_lazy as _
from markdownx.fields import MarkdownxFormField

from common.forms import PreviewImageInput

from .models import *


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["id", "item", "title", "body", "visibility"]
        widgets = {
            "item": forms.TextInput(attrs={"hidden": ""}),
        }

    title = forms.CharField(label=_("Title"))
    body = MarkdownxFormField(label=_("Content (Markdown)"), strip=False)
    share_to_mastodon = forms.BooleanField(
        label=_("Post to Fediverse"), initial=False, required=False
    )
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.RadioSelect,
    )


COLLABORATIVE_CHOICES = [
    (0, _("creator only")),
    (1, _("creator and their mutuals")),
]


class CollectionForm(forms.ModelForm):
    # id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    title = forms.CharField(label=_("Title"))
    brief = MarkdownxFormField(label=_("Content (Markdown)"), strip=False)
    # share_to_mastodon = forms.BooleanField(label=_("Repost to Fediverse"), initial=True, required=False)
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.RadioSelect,
    )
    collaborative = forms.TypedChoiceField(
        label=_("Collaborative editing"),
        initial=0,
        coerce=int,
        choices=COLLABORATIVE_CHOICES,
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Collection
        fields = [
            "title",
            "cover",
            "visibility",
            "collaborative",
            "brief",
        ]

        widgets = {
            "cover": PreviewImageInput(),
        }
