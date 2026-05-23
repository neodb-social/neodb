from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from core.models import Config


class ConfigOptionsTypeFilter(admin.SimpleListFilter):
    title = _("config options type")
    parameter_name = "type"

    def lookups(self, request, model_admin):
        return (
            ("system", _("System")),
            ("identity", _("Identity")),
            ("user", _("User")),
            ("domain", _("Domain")),
        )

    def queryset(self, request, queryset):
        match self.value():
            case "system":
                return queryset.filter(user__isnull=True, identity__isnull=True)
            case "identity":
                return queryset.exclude(identity__isnull=True)
            case "user":
                return queryset.exclude(user__isnull=True)
            case "domain":
                return queryset.exclude(domain__isnull=True)
            case _:
                return queryset


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ["id", "key", "user", "identity", "domain"]
    list_filter = (ConfigOptionsTypeFilter,)
    autocomplete_fields = ["user", "identity", "domain"]
