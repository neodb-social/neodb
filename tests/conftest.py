import pytest
from django.conf import settings

settings.STEAM_API_KEY = ""

# Marker for multi-database tests - equivalent to Django's databases = "__all__"
pytest.mark.all_databases = pytest.mark.django_db(databases="__all__", transaction=True)  # type: ignore
