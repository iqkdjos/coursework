from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    ApplicantProfile,
    LoginLog,
    OneTimeCode,
    RecoveryCode,
    TotpDevice,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-date_joined"]
    list_display = ("email", "role", "is_active", "max_linked", "date_joined", "last_login")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "profile__last_name", "profile__first_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Роли и доступ", {"fields": ("role", "is_active", "is_staff", "is_superuser",
                                     "groups", "user_permissions")}),
        ("MAX", {"fields": ("max_user_id", "max_linked_at", "max_notify")}),
        ("2FA", {"fields": (("totp_status",),)}),
    )
    readonly_fields = ("totp_status", "last_login", "date_joined")
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "password1", "password2", "role")}),
    )

    @admin.display(description="2FA")
    def totp_status(self, obj) -> str:
        device = getattr(obj, "totp", None)
        if device and device.confirmed:
            return "включена"
        return "не настроена"


@admin.register(ApplicantProfile)
class ApplicantProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "direction", "status", "bonus_points",
                    "total_exam_points", "updated_at")
    list_filter = ("status", "direction")
    search_fields = ("last_name", "first_name", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "identifier", "outcome", "ip_address")
    list_filter = ("outcome",)
    search_fields = ("identifier", "ip_address")
    readonly_fields = tuple(f.name for f in LoginLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TotpDevice)
class TotpDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "confirmed", "created_at", "confirmed_at")
    list_filter = ("confirmed",)
    readonly_fields = ("secret", "created_at", "confirmed_at", "last_step")

    def has_add_permission(self, request):
        return False


admin.site.register(RecoveryCode)
admin.site.register(OneTimeCode)
