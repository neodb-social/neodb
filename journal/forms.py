from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
        label=_("Crosspost to timeline"), initial=True, required=False
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
    (0, _("owner only")),
    (1, _("owner and their local mutuals")),
]


class CollectionForm(forms.ModelForm):
    # id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    title = forms.CharField(label=_("Title"))
    brief = MarkdownxFormField(label=_("Content (Markdown)"), strip=False)
    # share_to_mastodon = forms.BooleanField(label=_("Crosspost to timeline"), initial=True, required=False)
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


class MarkForm(forms.Form):
    status = forms.ChoiceField(
        choices=ShelfType.choices, required=False, label=_("Status")
    )
    text = forms.CharField(required=False, widget=forms.Textarea, label=_("Comment"))
    rating_grade = forms.IntegerField(
        required=False, min_value=0, max_value=10, label=_("Rating")
    )
    tags = forms.CharField(required=False, label=_("Tags"))
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.RadioSelect,
    )
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost to timeline"), initial=False, required=False
    )
    mark_anotherday = forms.BooleanField(required=False)
    mark_date = forms.CharField(required=False)

    def clean(self):
        cleaned_data = super().clean() or {}
        status_str = cleaned_data.get("status")
        try:
            status = ShelfType(status_str) if status_str else ShelfType.WISHLIST
        except ValueError:
            status = ShelfType.WISHLIST
        cleaned_data["status"] = status

        tags_str = cleaned_data.get("tags")
        cleaned_data["tags_list"] = (
            [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        )

        mark_date = None
        if cleaned_data.get("mark_anotherday"):
            shelf_time_offset = {
                ShelfType.WISHLIST: " 20:00:00",
                ShelfType.PROGRESS: " 21:00:00",
                ShelfType.DROPPED: " 21:30:00",
                ShelfType.COMPLETE: " 22:00:00",
            }

            dt_str = cleaned_data.get("mark_date", "")
            offset = shelf_time_offset.get(status, "")
            dt = parse_datetime(dt_str + offset)
            mark_date = (
                dt.replace(tzinfo=timezone.get_current_timezone()) if dt else None
            )
            if mark_date and mark_date >= timezone.now():
                mark_date = timezone.now()
        cleaned_data["mark_date_parsed"] = mark_date
        return cleaned_data
