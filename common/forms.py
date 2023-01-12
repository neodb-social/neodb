from django import forms
from markdownx.fields import MarkdownxFormField
import django.contrib.postgres.forms as postgres
from django.utils import formats
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import json


class KeyValueInput(forms.Widget):
    """
    Input widget for Json field
    """

    template_name = "widgets/hstore.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        data = None
        if context["widget"]["value"] is not None:
            data = json.loads(context["widget"]["value"])
        context["widget"]["value"] = (
            [{p[0]: p[1]} for p in data.items()] if data else []
        )
        return context

    class Media:
        js = ("js/key_value_input.js",)


class HstoreInput(forms.Widget):
    """
    Input widget for Hstore field
    """

    template_name = "widgets/hstore.html"

    def format_value(self, value):
        """
        Return a value as it should appear when rendered in a template.
        """
        if value == "" or value is None:
            return None
        if self.is_localized:
            return formats.localize_input(value)
        # do not return str
        return value

    class Media:
        js = ("js/key_value_input.js",)


class JSONField(forms.fields.JSONField):
    widget = KeyValueInput

    def to_python(self, value):
        if not value:
            return None
        j = {}
        if isinstance(value, dict):
            j = value
        else:
            pairs = json.loads("[" + value + "]")
            if isinstance(pairs, dict):
                j = pairs
            else:
                # list or tuple
                for pair in pairs:
                    j = {**j, **pair}
        return super().to_python(j)


class RadioBooleanField(forms.ChoiceField):
    widget = forms.RadioSelect

    def to_python(self, value):
        """Return a Python boolean object."""
        # Explicitly check for the string 'False', which is what a hidden field
        # will submit for False. Also check for '0', since this is what
        # RadioSelect will provide. Because bool("True") == bool('1') == True,
        # we don't need to handle that explicitly.
        if isinstance(value, str) and value.lower() in ("false", "0"):
            value = False
        else:
            value = bool(value)
        return value


class RatingValidator:
    """empty value is not validated"""

    def __call__(self, value):
        if not isinstance(value, int):
            raise ValidationError(
                _("%(value)s is not an integer"),
                params={"value": value},
            )
        if not str(value) in [str(i) for i in range(0, 11)]:
            raise ValidationError(
                _("%(value)s is not an integer in range 1-10"),
                params={"value": value},
            )


class PreviewImageInput(forms.FileInput):
    template_name = "widgets/image.html"

    def format_value(self, value):
        """
        Return the file object if it has a defined url attribute.
        """
        if self.is_initial(value):
            if value.url:
                return value.url
            else:
                return

    def is_initial(self, value):
        """
        Return whether value is considered to be initial value.
        """
        return bool(value and getattr(value, "url", False))


class TagInput(forms.TextInput):
    """
    Dump tag queryset into tag list
    """

    template_name = "widgets/tag.html"

    def format_value(self, value):
        if value == "" or value is None or len(value) == 0:
            return ""
        tag_list = []
        try:
            tag_list = [t["content"] for t in value]
        except TypeError:
            tag_list = [t.content for t in value]
        # return ','.join(tag_list)
        return tag_list

    class Media:
        css = {"all": ("lib/css/tag-input.css",)}
        js = ("lib/js/tag-input.js",)


class TagField(forms.CharField):
    """
    Split comma connected string into tag list
    """

    widget = TagInput

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return
        return [t.strip() for t in value.split(",")]


class MultiSelect(forms.SelectMultiple):
    template_name = "widgets/multi_select.html"

    class Media:
        css = {
            "all": (
                "https://cdn.jsdelivr.net/npm/multiple-select@1.5.2/dist/multiple-select.min.css",
            )
        }
        js = (
            "https://cdn.jsdelivr.net/npm/multiple-select@1.5.2/dist/multiple-select.min.js",
        )


class HstoreField(forms.CharField):
    widget = HstoreInput

    def to_python(self, value):
        if not value:
            return None
        # already in python types
        if isinstance(value, list):
            return value
        pairs = json.loads("[" + value + "]")
        return pairs


class DurationInput(forms.TextInput):
    """
    HH:mm:ss input widget
    """

    input_type = "time"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # context['widget']['type'] = self.input_type
        context["widget"]["attrs"]["step"] = "1"
        return context

    def format_value(self, value):
        """
        Given `value` is an integer in ms
        """
        ms = value
        if not ms:
            return super().format_value(None)
        x = ms // 1000
        seconds = x % 60
        x //= 60
        if x == 0:
            return super().format_value(f"00:00:{seconds:0>2}")
        minutes = x % 60
        x //= 60
        if x == 0:
            return super().format_value(f"00:{minutes:0>2}:{seconds:0>2}")
        hours = x % 24
        return super().format_value(f"{hours:0>2}:{minutes:0>2}:{seconds:0>2}")


class DurationField(forms.TimeField):
    widget = DurationInput

    def to_python(self, value):

        # empty value
        if value is None or value == "":
            return

        # if value is integer in ms
        if isinstance(value, int):
            return value

        # if value is string in time format
        h, m, s = value.split(":")
        return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000


#############################
# Form
#############################
VISIBILITY_CHOICES = [
    (0, _("公开")),
    (1, _("仅关注者")),
    (2, _("仅自己")),
]


class MarkForm(forms.ModelForm):
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    share_to_mastodon = forms.BooleanField(
        label=_("分享到联邦网络"), initial=True, required=False
    )
    rating = forms.IntegerField(
        label=_("评分"),
        validators=[RatingValidator()],
        widget=forms.HiddenInput(),
        required=False,
    )
    visibility = forms.TypedChoiceField(
        label=_("可见性"),
        initial=0,
        coerce=int,
        choices=VISIBILITY_CHOICES,
        widget=forms.RadioSelect,
    )
    tags = TagField(
        required=False,
        widget=TagInput(attrs={"placeholder": _("回车增加标签")}),
        label=_("标签"),
    )
    text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"placeholder": _("最多只能写360字哦~"), "maxlength": 360}
        ),
        label=_("短评"),
    )


class ReviewForm(forms.ModelForm):
    title = forms.CharField(label=_("标题"))
    content = MarkdownxFormField(label=_("正文 (Markdown)"))
    share_to_mastodon = forms.BooleanField(
        label=_("分享到联邦网络"), initial=True, required=False
    )
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    visibility = forms.TypedChoiceField(
        label=_("可见性"),
        initial=0,
        coerce=int,
        choices=VISIBILITY_CHOICES,
        widget=forms.RadioSelect,
    )
