import json

import django.contrib.postgres.forms as postgres
from django import forms
from django.core.exceptions import ValidationError
from django.utils import formats
from django.utils.translation import gettext_lazy as _
from markdownx.fields import MarkdownxFormField


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
