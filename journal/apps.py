from django.apps import AppConfig


class JournalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "journal"

    def ready(self):
        # load key modules in proper order, make sure class inject and signal works as expected
        from . import api
        from .models import Tag, Rating
        from catalog.models import Indexer

        Indexer.register_list_model(Tag)
        Indexer.register_piece_model(Rating)
