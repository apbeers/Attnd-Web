from django.contrib import admin

from console.models import Order, TwoFactorDevice


class OrderAdmin(admin.ModelAdmin):
    list_filter = ['user', 'event']


admin.site.register(Order, OrderAdmin)


# Accounts: people are known by their email; the username is an internal, generated id.
from django.contrib.auth.admin import UserAdmin  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402

admin.site.unregister(User)


@admin.register(User)
class AccountAdmin(UserAdmin):
    list_display = ('email', 'is_superuser', 'is_active', 'date_joined', 'last_login', 'username')
    search_fields = ('email', 'username')
    ordering = ('email',)


@admin.register(TwoFactorDevice)
class TwoFactorDeviceAdmin(admin.ModelAdmin):
    """Superusers can see who uses two-factor sign-in and delete a device (same as `reset_2fa`)."""
    list_display = ('user', 'confirmed', 'confirmed_at', 'last_used_at')
    search_fields = ('user__email',)
    fields = ('user', 'confirmed', 'confirmed_at', 'last_used_at')
    readonly_fields = fields  # never show or edit the secret or recovery code hashes

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
