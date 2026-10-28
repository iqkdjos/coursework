from django.contrib import admin

from .models import Enrollment, Event


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    raw_id_fields = ("applicant",)
    readonly_fields = ("registered_at",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "event_type", "start_at", "status", "capacity",
                    "enrollments_count", "bonus_points", "created_by")
    list_filter = ("status", "event_type")
    search_fields = ("title", "description", "location")
    inlines = [EnrollmentInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("applicant", "event", "status", "points", "points_set_at", "registered_at")
    list_filter = ("status",)
    raw_id_fields = ("applicant", "event", "points_set_by")
