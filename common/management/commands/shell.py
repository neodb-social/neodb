from django.core.management.commands import shell


class Command(shell.Command):
    def get_auto_imports(self):
        imps = super().get_auto_imports()
        imps.remove("catalog.models.collection.Collection")
        return imps
