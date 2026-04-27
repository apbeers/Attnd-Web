import base64
import io

import qrcode
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from events.models import Event, Location, Notification, Device


class UserScopedAdminMixin:
    """Filters all querysets and permissions to the logged-in user. Superusers see everything."""

    def has_module_permission(self, request):
        return True

    def has_add_permission(self, request):
        return True

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return True
        return request.user.is_superuser or obj.user == request.user

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
    exclude = ['user']  # set automatically in EventAdmin.save_formset


class NotificationInline(admin.TabularInline):
    model = Notification
    extra = 1
    exclude = ['user']  # set automatically in EventAdmin.save_formset


class EventAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    inlines = [LocationInline, NotificationInline]
    list_display = ['name', 'event_code', 'start_time', 'address', 'paid', 'user']
    list_filter = ['paid', 'user']
    search_fields = ['name', 'event_code', 'address']
    date_hierarchy = 'start_time'
    fields = [
        'name', 'event_code', 'description', 'latitude', 'longitude',
        'start_time', 'paid', 'public', 'flyer', 'address', 'order_id',
        'user', 'admin_qr_code',
    ]

    def get_readonly_fields(self, request, obj=None):
        ro = ['admin_qr_code']
        if not request.user.is_superuser:
            ro += ['user', 'paid']
        return ro

    def admin_qr_code(self, obj):
        url = f'https://attnd.app/join/{obj.event_code}/'
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        return format_html('<img src="data:image/png;base64,{}" width="200">', b64)

    admin_qr_code.short_description = 'QR Code'

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if not instance.pk:
                instance.user = request.user
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


class LocationAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    list_display = ['name', 'event', 'address', 'user']
    list_filter = ['user', 'event']
    search_fields = ['name', 'address', 'event__name']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'event' and not request.user.is_superuser:
            kwargs['queryset'] = Event.objects.filter(user=request.user)
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(pk=request.user.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class NotificationAdmin(UserScopedAdminMixin, admin.ModelAdmin):
    list_display = ['title', 'event', 'send_time', 'sent', 'user']
    list_filter = ['sent', 'user', 'event']
    search_fields = ['title', 'body', 'event__name']
    date_hierarchy = 'send_time'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'event' and not request.user.is_superuser:
            kwargs['queryset'] = Event.objects.filter(user=request.user)
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(pk=request.user.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class DeviceAdmin(admin.ModelAdmin):
    list_display = ['id', 'event', 'platform', 'subscribed']
    list_filter = ['platform', 'subscribed', 'event']
    search_fields = ['id']

    def has_module_permission(self, request):
        return request.user.is_superuser


class RestrictedUserAdmin(BaseUserAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser


admin.site.register(Event, EventAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Notification, NotificationAdmin)
admin.site.register(Device, DeviceAdmin)

admin.site.unregister(User)
admin.site.register(User, RestrictedUserAdmin)
