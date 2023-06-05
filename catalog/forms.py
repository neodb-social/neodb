from django import forms
from django.utils.translation import gettext_lazy as _
from catalog.models import *
from common.forms import PreviewImageInput


CatalogForms = {}


def _EditForm(item_model: Item):
    item_fields = (
        ["id"]
        + item_model.METADATA_COPY_LIST
        + ["cover"]
        + ["primary_lookup_id_type", "primary_lookup_id_value"]
    )
    if "media" in item_fields:
        # FIXME not sure why this field is always duplicated
        item_fields.remove("media")

    class EditForm(forms.ModelForm):
        id = forms.IntegerField(required=False, widget=forms.HiddenInput())
        primary_lookup_id_type = forms.ChoiceField(
            required=False,
            choices=item_model.lookup_id_type_choices(),
            label=_("主要标识类型"),
        )
        primary_lookup_id_value = forms.CharField(
            required=False, label=_("主要标识数据通常由系统自动检测，请勿随意更改现有值，新增条目不确定留空即可")
        )

        class Meta:
            model = item_model
            fields = item_fields
            widgets = {
                "cover": PreviewImageInput(),
            }

        def clean(self):
            data = super().clean()
            t, v = self.Meta.model.lookup_id_cleanup(
                data.get("primary_lookup_id_type"), data.get("primary_lookup_id_value")
            )
            data["primary_lookup_id_type"] = t
            data["primary_lookup_id_value"] = v
            return data

    return EditForm


def init_forms():
    for cls in Item.__subclasses__():
        CatalogForms[cls.__name__] = _EditForm(cls)


init_forms()
