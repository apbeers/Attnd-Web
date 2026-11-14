from django.contrib import admin

from console.models import Order


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
