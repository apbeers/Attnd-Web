from django.contrib import admin
from events.models import Event, Location, Notification


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


admin.site.register(Event, EventAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Notification)
