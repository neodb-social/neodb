from django import forms
from django.utils.translation import gettext_lazy as _

from common.forms import PreviewImageInput

from .models import Report


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = [
            "reported_user",
            "image",
            "message",
        ]
        widgets = {
            "message": forms.Textarea(attrs={"placeholder": _("详情")}),
            "image": PreviewImageInput(),
            "reported_user": forms.HiddenInput(),
        }
        labels = {"reported_user": _("投诉对象"), "image": _("相关证据"), "message": _("详情")}
