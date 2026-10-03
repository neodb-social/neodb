from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from typing import Any

from common.forms import PreviewImageInput
from common.models.lang import LOCALE_CHOICES
from users.models import User

from .models import *


COMMENT_TIPS = _(
    "Tips: use >!text!< for spoilers; some instances may not be able to show posts longer than 360 charactors."
)


def _post_language_choices() -> list[tuple[str, Any]]:
    # LOCALE_CHOICES is rebuilt in place on a SiteConfig change; a list given to
    # the field directly would be copied at import time
    return LOCALE_CHOICES


class PostLanguageField(forms.ChoiceField):
    """Language of the post a piece publishes.

    A form without this field in its POST cleans to None, which keeps the
    language of the existing post; "x" (unknown) cleans to "".
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            choices=_post_language_choices,
            required=False,
            label=_("Language"),
            widget=forms.Select(attrs={"aria-label": _("Language")}),
            **kwargs,
        )

    def clean(self, value: Any) -> str | None:
        value = super().clean(value)
        if not value:
            return None
        return "" if value == "x" else value


def post_language_initial(user: User, piece: Piece | None = None) -> str:
    post = piece.latest_post if piece else None
    return (post.language if post else user.macrolanguage) or "x"


VISIBILITY_WIDGET_ATTRS = {"aria-label": _("Visibility")}


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["id", "item", "title", "body", "visibility"]
        widgets = {
            "item": forms.TextInput(attrs={"hidden": ""}),
        }

    # Labels are pinned for screen-reader / form-error association; the visible
    # UI surfaces them as ``placeholder`` + ``aria-label`` instead.
    title = forms.CharField(
        label=_("Title"),
        widget=forms.TextInput(
            attrs={"placeholder": _("Title"), "aria-label": _("Title")}
        ),
    )
    body = forms.CharField(
        label=_("Content (Markdown)"),
        strip=False,
        widget=forms.Textarea(
            attrs={
                "class": "easymde-editor",
                "placeholder": _("Content (Markdown)"),
                "aria-label": _("Content (Markdown)"),
            }
        ),
    )
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost"),
        help_text=_("Crosspost to your connected social networks"),
        initial=False,
        required=False,
    )
    leading_space = forms.BooleanField(
        label=_("Keep leading spaces"),
        help_text=_("When saving, replace leading spaces with full-width spaces"),
        required=False,
        initial=False,
    )
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.Select(attrs=VISIBILITY_WIDGET_ATTRS),
    )
    language = PostLanguageField()


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["id", "title", "cover", "body", "summary", "sensitive", "visibility"]
        widgets = {"cover": PreviewImageInput()}
        labels = {"cover": _("Featured image (optional)")}

    # Labels are pinned for screen-reader / form-error association; the visible
    # UI surfaces them as ``placeholder`` + ``aria-label`` instead.
    title = forms.CharField(
        label=_("Title"),
        max_length=500,
        widget=forms.TextInput(
            attrs={"placeholder": _("Title"), "aria-label": _("Title")}
        ),
    )
    summary = forms.CharField(
        label=_("Summary (optional)"),
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": _("Summary (optional)"),
                "aria-label": _("Summary (optional)"),
            }
        ),
    )
    body = forms.CharField(
        label=_("Content (Markdown)"),
        strip=False,
        widget=forms.Textarea(
            attrs={
                "class": "easymde-editor",
                "placeholder": _("Content (Markdown)"),
                "aria-label": _("Content (Markdown)"),
            }
        ),
    )
    tags = forms.CharField(
        label=_("Tags (comma separated)"),
        required=False,
        max_length=2000,
        widget=forms.TextInput(
            attrs={
                "placeholder": _("Tags (comma separated)"),
                "aria-label": _("Tags (comma separated)"),
            }
        ),
    )
    sensitive = forms.BooleanField(
        label=_("Mark as sensitive"), required=False, initial=False
    )
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost"),
        help_text=_("Crosspost to your connected social networks"),
        initial=False,
        required=False,
    )
    leading_space = forms.BooleanField(
        label=_("Keep leading spaces"),
        help_text=_("When saving, replace leading spaces with full-width spaces"),
        required=False,
        initial=False,
    )
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.Select(attrs=VISIBILITY_WIDGET_ATTRS),
    )
    language = PostLanguageField()


COLLABORATIVE_CHOICES = [
    (0, _("Owner only")),
    (1, _("Owner and their local mutuals")),
]


class CollectionForm(forms.ModelForm):
    # id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    title = forms.CharField(label=_("Title"))
    brief = forms.CharField(
        label=_("Content (Markdown)"),
        strip=False,
        required=False,
        widget=forms.Textarea(attrs={"class": "easymde-editor"}),
    )
    # share_to_mastodon = forms.BooleanField(label=_("Crosspost"), initial=True, required=False)
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


class CollaboratorCollectionForm(CollectionForm):
    """Collection form for non-owner editors: owner-only settings
    (visibility, collaborative) are removed so collaborators can edit
    content but not privacy or collaboration policy."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        del self.fields["visibility"]
        del self.fields["collaborative"]


class MarkForm(forms.Form):
    status = forms.ChoiceField(
        choices=ShelfType.choices, required=False, label=_("Status")
    )
    text = forms.CharField(
        required=False,
        strip=False,
        label=_("Comment"),
        widget=forms.Textarea(
            attrs={"rows": 5, "autofocus": True, "placeholder": COMMENT_TIPS}
        ),
    )
    rating_grade = forms.IntegerField(
        required=False, min_value=0, max_value=10, label=_("Rating")
    )
    tags = forms.CharField(required=False, label=_("Tags"))
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.Select(attrs=VISIBILITY_WIDGET_ATTRS),
    )
    language = PostLanguageField()
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost"),
        help_text=_("Crosspost to your connected social networks"),
        widget=forms.CheckboxInput(attrs={"role": "switch"}),
        initial=False,
        required=False,
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


class CommentForm(forms.Form):
    # strip=False keeps the raw textarea value, as the pre-form view did
    text = forms.CharField(
        required=False,
        strip=False,
        label=_("Comment"),
        widget=forms.Textarea(
            attrs={"cols": 40, "rows": 10, "placeholder": COMMENT_TIPS}
        ),
    )
    visibility = forms.TypedChoiceField(
        label=_("Visibility"),
        initial=0,
        coerce=int,
        choices=VisibilityType.choices,
        widget=forms.Select(attrs=VISIBILITY_WIDGET_ATTRS),
    )
    language = PostLanguageField()
    share_to_mastodon = forms.BooleanField(
        label=_("Crosspost"),
        help_text=_("Crosspost to your connected social networks"),
        widget=forms.CheckboxInput(attrs={"role": "switch"}),
        initial=False,
        required=False,
    )
    # raw "hh:mm:ss" playback position; only meaningful for podcast episodes,
    # so the view parses it against the item type
    position = forms.CharField(required=False)
