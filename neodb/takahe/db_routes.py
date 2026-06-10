from django.conf import settings

_is_testing = "testserver" in settings.ALLOWED_HOSTS


class TakaheRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "takahe":
            return "takahe"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == "takahe":
            return "takahe"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # skip this check but please make sure
        # not create relations between takahe models and other apps
        if obj1._meta.app_label == "takahe" or obj2._meta.app_label == "takahe":
            return obj1._meta.app_label == obj2._meta.app_label
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == "takahe" or db == "takahe":
            return _is_testing and app_label == db
        return None
