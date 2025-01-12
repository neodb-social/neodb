from django.apps import AppConfig


class JournalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "journal"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        from catalog.models import Indexer

        from . import api  # noqa
        from .models import Rating, Tag

        Indexer.register_list_model(Tag)
        Indexer.register_piece_model(Rating)
