from django import forms
from django.utils.translation import gettext_lazy as _

from catalog.models import *
from common.forms import PreviewImageInput
from common.models import SITE_DEFAULT_LANGUAGE, detect_language, uniq
from common.models.genre import normalize_genres
from common.models.lang import normalize_languages

CatalogForms = {}


def _EditForm(item_model):
    item_fields = (
        ["id"]
        + list(item_model.METADATA_COPY_LIST)
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
            label=_("Primary ID Type"),
            help_text="automatically detected, usually no change necessary",
        )
        primary_lookup_id_value = forms.CharField(
            required=False,
            label=_("Primary ID Value"),
            help_text="automatically detected, usually no change necessary, left empty if unsure",
        )

        class Meta:
            model = item_model
            fields = item_fields
            widgets = {
                "cover": PreviewImageInput(),
            }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.migrate_initial()
            self.canonicalize_credit_initials()

        def migrate_initial(self):
            if self.initial and self.instance and self.instance.pk:
                if (
                    "localized_title" in self.Meta.fields
                    and not self.initial["localized_title"]
                ):
                    titles = []
                    if self.instance.title:
                        titles.append(
                            {
                                "lang": detect_language(self.instance.title),
                                "text": self.instance.title,
                            }
                        )
                    if (
                        hasattr(self.instance, "orig_title")
                        and self.instance.orig_title
                    ):
                        titles.append(
                            {
                                "lang": detect_language(self.instance.orig_title),
                                "text": self.instance.orig_title,
                            }
                        )
                    if (
                        hasattr(self.instance, "other_title")
                        and self.instance.other_title
                    ):
                        for t in self.instance.other_title:
                            titles.append({"lang": detect_language(t), "text": t})
                    if not titles:
                        titles = [{"lang": SITE_DEFAULT_LANGUAGE, "text": "<no title>"}]
                    self.initial["localized_title"] = uniq(titles)
                if (
                    "localized_description" in self.Meta.fields
                    and not self.initial["localized_description"]
                ):
                    if self.instance.brief:
                        d = {
                            "lang": detect_language(self.instance.brief),
                            "text": self.instance.brief,
                        }
                    else:
                        d = {
                            "lang": self.initial["localized_title"][0]["lang"],
                            "text": "",
                        }
                    self.initial["localized_description"] = [d]
                # if (
                #     "language" in self.Meta.fields
                #     and self.initial["language"]
                # ):
                #     if isinstance(self.initial["language"], str):

        def canonicalize_credit_initials(self):
            """Display-only: replace credit names with /person|organization/<uuid>
            in initial form values when an ItemCredit already links them to a
            People. Does not mutate the instance or the database; a later save
            persists whatever the user submits back.
            """
            instance = self.instance
            if not instance or not instance.pk:
                return
            mapping = getattr(type(instance), "CREDIT_FIELD_MAPPING", {}) or {}
            if not mapping:
                return
            roles_in_form = {r for r in mapping.values()}
            if not roles_in_form:
                return
            credits = instance.credits.filter(
                role__in=roles_in_form, person__isnull=False
            ).select_related("person")
            linked: dict[tuple[str, str], str] = {}
            for c in credits:
                if c.person:
                    linked[(c.role, c.name)] = c.person.url
            if not linked:
                return
            for field_name, role in mapping.items():
                if field_name not in self.fields:
                    continue
                values = self.initial.get(field_name)
                if not values:
                    continue
                scalar_field = isinstance(values, str)
                iter_values = [values] if scalar_field else values
                new_values: list[str | dict] = []
                for v in iter_values:
                    if isinstance(v, dict):
                        name = v.get("name") or ""
                        canonical = linked.get((role, name))
                        new_values.append({**v, "name": canonical} if canonical else v)
                    else:
                        name = str(v or "")
                        canonical = linked.get((role, name))
                        new_values.append(canonical if canonical else v)
                if scalar_field:
                    self.initial[field_name] = new_values[0] if new_values else ""
                else:
                    self.initial[field_name] = new_values

        def clean(self):
            data = super().clean() or {}
            t, v = self.Meta.model.lookup_id_cleanup(
                data.get("primary_lookup_id_type"), data.get("primary_lookup_id_value")
            )
            data["primary_lookup_id_type"] = t
            data["primary_lookup_id_value"] = v
            if "language" in data:
                data["language"] = normalize_languages(data["language"])
            if "genre" in data:
                data["genre"] = normalize_genres(data["genre"])
            return data

    return EditForm


def init_forms():
    for cls in Item.__subclasses__():
        CatalogForms[cls.__name__] = _EditForm(cls)


init_forms()
