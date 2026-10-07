"""ASGI entry point for the dedicated Mastodon streaming service."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "takahe.settings")
django.setup()

from api.streaming import StreamingApplication  # noqa: E402

application = StreamingApplication()
