import re

from django.db import models
from django.shortcuts import reverse
from django.utils.translation import gettext_lazy as _
from markdown import markdown
from markdownx.models import MarkdownxField

RE_HTML_TAG = re.compile(r"<[^>]*>")


class Announcement(models.Model):
    """Model definition for Announcement."""

    title = models.CharField(max_length=200)
    content = MarkdownxField()
    slug = models.SlugField(
        max_length=300, allow_unicode=True, unique=True, null=True, blank=True
    )
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta definition for Announcement."""

        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"

    def get_absolute_url(self):
        return reverse("management:retrieve", kwargs={"pk": self.pk})

    def get_html_content(self):
        html = markdown(self.content)
        return html

    def get_plain_content(self):
        """
        Get plain text format content
        """
        return RE_HTML_TAG.sub(" ", self.get_html_content())

    def __str__(self):
        """Unicode representation of Announcement."""
        return self.title
