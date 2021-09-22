from django.contrib import admin

from events.models import Event, Location, Notification, Device


class LocationInline(admin.TabularInline):
    model = Location
    extra = 1


class NotificationInline(admin.TabularInline):
    model = Notification
    extra = 1


class EventAdmin(admin.ModelAdmin):
    inlines = [LocationInline, NotificationInline]
    list_filter = ['user']


class LocationAdmin(admin.ModelAdmin):
    list_filter = ['user', 'event']


class NotificationAdmin(admin.ModelAdmin):
    list_filter = ['user', 'event']


class DeviceAdmin(admin.ModelAdmin):
    list_filter = ['event', 'subscribed']


admin.site.register(Event, EventAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Notification)
admin.site.register(Device, DeviceAdmin)
