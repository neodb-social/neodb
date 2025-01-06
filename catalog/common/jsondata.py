# pyright: reportIncompatibleMethodOverride=false
import copy
from base64 import b64decode, b64encode
from datetime import date, datetime
from functools import partialmethod
from hashlib import sha256
from importlib import import_module

import django
import loguru
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import FieldError
from django.db.models import DEFERRED, fields  # type:ignore
from django.utils import dateparse, timezone
from django.utils.encoding import force_bytes
from django.utils.translation import gettext_lazy as _
from django_jsonform.forms.fields import JSONFormField as DJANGO_JSONFormField

# from django.db.models import JSONField as DJANGO_JSONField
# from jsoneditor.fields.django3_jsonfield import JSONField as DJANGO_JSONField
# from django.contrib.postgres.fields import ArrayField as DJANGO_ArrayField
from django_jsonform.models.fields import ArrayField as DJANGO_ArrayField
from django_jsonform.models.fields import JSONField as DJANGO_JSONField
from loguru import logger


class Patched_DJANGO_JSONField(DJANGO_JSONField):
    def formfield(self, **kwargs):
        schema = getattr(self.model, self.attname + "_schema", self.schema)
        return super().formfield(
            **{
                "form_class": DJANGO_JSONFormField,
                "schema": schema,
                "model_name": self.model.__name__,
                "file_handler": self.file_handler,
                **kwargs,
            }
        )


def _get_crypter():
    configured_keys = [settings.SECRET_KEY] + settings.SECRET_KEY_FALLBACKS
    keys = [Fernet(b64encode(sha256(force_bytes(k)).digest())) for k in configured_keys]
    if len(keys) == 0:
        raise ValueError("No keys defined")
    return MultiFernet(keys)


CRYPTER = _get_crypter()


def encrypt_str(s: str) -> str:
    # be sure to encode the string to bytes
    return CRYPTER.encrypt(s.encode("utf-8")).decode("utf-8")


def decrypt_str(t: str) -> str:
    # be sure to decode the bytes to a string
    return CRYPTER.decrypt(t.encode("utf-8")).decode("utf-8")


__all__ = (
    "BooleanField",
    "CharField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "DurationField",
    "EmailField",
    "FloatField",
    "IntegerField",
    "IPAddressField",
    "GenericIPAddressField",
    "NullBooleanField",
    "EncryptedTextField",
    "TextField",
    "TimeField",
    "URLField",
    "ArrayField",
    "JSONField",
)


class JSONFieldDescriptor(object):
    def __init__(self, field):
        self.field = field

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        json_value = getattr(instance, self.field.json_field_name)
        if isinstance(json_value, dict):
            if self.field.attname in json_value:
                value = json_value.get(self.field.attname, None)
                if hasattr(self.field, "from_json"):
                    value = self.field.from_json(value)
            elif self.field.has_default():
                value = self.field._get_default()
                # if hasattr(self.field, "to_json"):
                #     json_value[self.field.attname] = self.field.to_json(value)
                # else:
                #     json_value[self.field.attname] = value
                # return value
            else:
                value = None
            return value
        return None

    def __set__(self, instance, value):
        json_value = getattr(instance, self.field.json_field_name)
        if json_value:
            assert isinstance(json_value, dict)
        else:
            json_value = {}

        if hasattr(self.field, "to_json"):
            value = self.field.to_json(value)

        if not value and self.field.blank and not self.field.null:
            try:
                del json_value[self.field.attname]
            except KeyError:
                pass
        else:
            json_value[self.field.attname] = value

        setattr(instance, self.field.json_field_name, json_value)


class JSONFieldMixin(object):
    """
    Override django.db.model.fields.Field.contribute_to_class
    to make a field always private, and register custom access descriptor
    """

    def __init__(self, *args, **kwargs):
        self.json_field_name = kwargs.pop("json_field_name", "metadata")
        super(JSONFieldMixin, self).__init__(*args, **kwargs)

    def contribute_to_class(self: "fields.Field", cls, name, private_only=False):  # type: ignore
        self.set_attributes_from_name(name)
        self.model = cls
        self.concrete = False
        self.column = None  # type: ignore
        cls._meta.add_field(self, private=True)

        if not getattr(cls, self.attname, None):
            descriptor = JSONFieldDescriptor(self)
            setattr(cls, self.attname, descriptor)

        if self.choices is not None:
            setattr(
                cls,
                "get_%s_display" % self.name,
                partialmethod(cls._get_FIELD_display, field=self),
            )

        self.column = self.json_field_name  # type: ignore

    def get_lookup(self, lookup_name):
        # Always return None, to make get_transform been called
        return None

    def get_transform(self, lookup_name):
        class TransformFactoryWrapper:
            def __init__(self, json_field, transform, original_lookup):
                self.json_field = json_field
                self.transform = transform
                self.original_lookup = original_lookup

            def __call__(self, lhs, **kwargs):
                lhs = copy.copy(lhs)
                lhs.target = self.json_field
                lhs.output_field = self.json_field
                transform = self.transform(lhs, **kwargs)
                transform._original_get_lookup = transform.get_lookup
                transform.get_lookup = lambda name: transform._original_get_lookup(
                    self.original_lookup
                )
                return transform

        json_field = self.model._meta.get_field(self.json_field_name)
        transform = json_field.get_transform(self.name)  # type: ignore
        if transform is None:
            raise FieldError(
                "JSONField '%s' has no support for key '%s' %s lookup"
                % (self.json_field_name, self.name, lookup_name)  # type: ignore
            )

        return TransformFactoryWrapper(json_field, transform, lookup_name)

    def get_default(self):
        # deferred during obj initialization so it don't overwrite json with default value
        return DEFERRED


class BooleanField(JSONFieldMixin, fields.BooleanField):
    pass
    # def __init__(self, *args, **kwargs):
    #     super(BooleanField, self).__init__(*args, **kwargs)
    #     if django.VERSION < (2,):
    #         self.blank = False


class CharField(JSONFieldMixin, fields.CharField):
    def from_json(
        self, value
    ):  # TODO workaound some bad data in migration, should be removed after clean up
        return value if isinstance(value, str) else None

    pass


class DateField(JSONFieldMixin, fields.DateField):
    def to_json(self, value):
        if value:
            if not isinstance(value, (datetime, date)):
                value = dateparse.parse_date(value)
            return value.strftime("%Y-%m-%d") if value else None

    def from_json(self, value):
        if value is not None:
            return dateparse.parse_date(value)


class DateTimeField(JSONFieldMixin, fields.DateTimeField):
    def to_json(self, value: datetime | date | str):
        if value:
            if not isinstance(value, (datetime, date)):
                v = dateparse.parse_date(value)
                if v is None:
                    raise ValueError(
                        f"DateTimeField: '{value}' has invalid datatime format"
                    )
                value = v
            if isinstance(value, date):
                value = datetime.combine(value, datetime.min.time())
            if not timezone.is_aware(value):
                value = timezone.make_aware(value)
            return value.isoformat()

    def from_json(self, value):
        if value:
            return dateparse.parse_datetime(value)


class DecimalField(JSONFieldMixin, fields.DecimalField):
    pass


class EmailField(JSONFieldMixin, fields.EmailField):
    pass


class FloatField(JSONFieldMixin, fields.FloatField):
    pass


class IntegerField(JSONFieldMixin, fields.IntegerField):
    pass


class IPAddressField(JSONFieldMixin, fields.IPAddressField):
    pass


class GenericIPAddressField(JSONFieldMixin, fields.GenericIPAddressField):
    pass


class NullBooleanField(JSONFieldMixin, fields.NullBooleanField):
    pass


class TextField(JSONFieldMixin, fields.TextField):
    pass


class EncryptedTextField(JSONFieldMixin, fields.TextField):
    def to_json(self, value):
        if value:
            return encrypt_str(str(value))

    def from_json(self, value):
        if value:
            return decrypt_str(value)


class TimeField(JSONFieldMixin, fields.TimeField):
    def to_json(self, value):
        if value:
            if not timezone.is_aware(value):
                value = timezone.make_aware(value)
            return value.isoformat()

    def from_json(self, value):
        if value:
            return dateparse.parse_time(value)


class URLField(JSONFieldMixin, fields.URLField):
    pass


class ArrayField(JSONFieldMixin, DJANGO_ArrayField):
    # def __init__(self, *args, **kwargs):
    #     kwargs["help_text"] = _("comma separated list of values")
    #     super().__init__(*args, **kwargs)

    def from_json(self: "fields.Field", value):  # type:ignore
        if value:
            if isinstance(value, list):
                return value
            else:  # backward compatible with dirty legacy data
                # logger.error(f"ArrayField has irregular value: {self.name}: {value}")
                return [value]
        return []


class JSONField(JSONFieldMixin, Patched_DJANGO_JSONField):
    pass


class DurationField(JSONFieldMixin, fields.DurationField):
    pass
