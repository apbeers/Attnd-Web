from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from events.models import Event, Location, Notification, Device


class UserScopedAdminMixin:
    """Filters all querysets and permissions to the logged-in user. Superusers see everything."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return True
        return request.user.is_superuser or obj.user == request.user

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return True
        return request.user.is_superuser or obj.user == request.user

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return ['user']
        return []


class LocationInline(admin.TabularInline):
    model = Location
    extra = 1

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.user = request.user
        super().save_model(request, obj, form, change)


class NotificationInline(admin.TabularInline):
    model = Notification
    extra = 1

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.user = request.user
        super().save_model(request, obj, form, change)


class EventAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    inlines = [LocationInline, NotificationInline]
    list_filter = ['user']


class LocationAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    list_filter = ['user', 'event']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'event' and not request.user.is_superuser:
            kwargs['queryset'] = Event.objects.filter(user=request.user)
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(pk=request.user.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class NotificationAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    list_filter = ['user', 'event']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'event' and not request.user.is_superuser:
            kwargs['queryset'] = Event.objects.filter(user=request.user)
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(pk=request.user.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class DeviceAdmin(admin.ModelAdmin):
    list_filter = ['event', 'subscribed']

    def has_module_perms(self, request):
        return request.user.is_superuser


class RestrictedUserAdmin(BaseUserAdmin):
    def has_module_perms(self, request):
        return request.user.is_superuser


admin.site.register(Event, EventAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Notification, NotificationAdmin)
admin.site.register(Device, DeviceAdmin)

admin.site.unregister(User)
admin.site.register(User, RestrictedUserAdmin)
