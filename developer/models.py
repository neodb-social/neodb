from django.db import models
from django.core.validators import RegexValidator
from oauth2_provider.models import AbstractApplication
from markdownx.models import MarkdownxField


class Application(AbstractApplication):
    name = models.CharField(
        max_length=255,
        blank=False,
        validators=[
            RegexValidator(
                regex=r"^\w[\w_\-. ]*\w$",
                message="至少两个字，不可包含普通文字和-_.以外的字符",
            ),
        ],
        unique=True,
    )
    descrpition = MarkdownxField(default="", blank=True)
    url = models.URLField(null=True, blank=True)
    is_official = models.BooleanField(default=False)
