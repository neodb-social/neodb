from django.contrib import admin

from common.models import DurableDispatch


@admin.register(DurableDispatch)
class DurableDispatchAdmin(admin.ModelAdmin):
    list_display = (
        "responsibility_ref",
        "queue",
        "state",
        "attempt_count",
        "last_attempt_at",
        "next_attempt_at",
        "lease_expires_at",
        "last_outcome",
        "retry_eligible_display",
        "apparently_stuck_display",
    )
    list_filter = ("queue", "state", "last_outcome")
    search_fields = ("responsibility_ref", "last_error_category", "last_error_text")
    readonly_fields = (
        "responsibility_ref",
        "queue",
        "state",
        "attempt_count",
        "max_attempts",
        "last_attempt_at",
        "next_attempt_at",
        "lease_expires_at",
        "last_outcome",
        "last_error_category",
        "last_error_text",
        "last_error_at",
        "created_at",
        "updated_at",
        "retry_eligible_display",
        "apparently_stuck_display",
    )
    fields = readonly_fields
    ordering = ("state", "next_attempt_at", "id")

    @admin.display(boolean=True, description="Retry eligible")
    def retry_eligible_display(self, obj):
        return obj.retry_eligible

    @admin.display(boolean=True, description="Apparently stuck")
    def apparently_stuck_display(self, obj):
        return obj.apparently_stuck

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
