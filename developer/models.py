from django.db import models
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _
from oauth2_provider.models import AbstractApplication
from markdownx.models import MarkdownxField
from journal.renderers import render_md


class Application(AbstractApplication):
    name = models.CharField(
        max_length=255,
        blank=False,
        validators=[
            RegexValidator(
                regex=r"^\w[\w_\-. ]*\w$",
                message=_(
                    "minimum two characters, words and -_. only, no special characters"
                ),
            ),
        ],
    )
    description = MarkdownxField(default="", blank=True)
    url = models.URLField(null=True, blank=True)
    is_official = models.BooleanField(default=False)
    unique_together = [["user", "name"]]

    def description_html(self):
        return render_md(self.description)
