from typing import Any

from django.db.models import Manager, QuerySet
from django.db.models.fields.files import FieldFile
from pydantic import ConfigDict, field_validator, ValidationInfo, BaseModel
from pydantic import Field  # noqa


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*")
    @classmethod
    def django_validator(cls, value: Any, info: ValidationInfo):
        if isinstance(value, Manager):
            return list(value.all())

        elif isinstance(value, getattr(QuerySet, "__origin__", QuerySet)):
            return list(value)

        if callable(value):
            return value()

        elif isinstance(value, FieldFile):
            if not value:
                return None
            return value.url

        return value
