from django.contrib import admin

from api.models import Application, PushNotification, PushSubscription, Token


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "website", "created"]
    search_fields = ["name", "website"]


class PushSubscriptionInline(admin.TabularInline):
    model = PushSubscription
    extra = 0


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "identity", "application", "created"]
    autocomplete_fields = ["user", "identity", "application"]
    inlines = [PushSubscriptionInline]


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["id", "token", "endpoint", "policy"]
    search_fields = ["endpoint"]
    raw_id_fields = ["token"]


@admin.register(PushNotification)
class PushNotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "token", "type", "title", "body", "state"]
    list_filter = ["state"]
    raw_id_fields = ["token"]
