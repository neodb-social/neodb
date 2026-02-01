from django.core.management.commands import shell


class Command(shell.Command):
    def get_auto_imports(self):
        imps = super().get_auto_imports()
        if imps is not None:
            imps.remove("catalog.models.collection.Collection")
        return imps
