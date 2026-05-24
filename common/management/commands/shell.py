from django.core.management.commands import shell

from common.models import SiteConfig


class Command(shell.Command):
    def get_auto_imports(self):
        imps = super().get_auto_imports()
        if imps is not None:
            imps.remove("catalog.models.collection.Collection")
        return imps

    def handle(self, **options):
        SiteConfig.reload()
        return super().handle(**options)
