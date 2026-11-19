"""Django admin for staff: plain ModelAdmins (superusers, or staff with model permissions).
Organizers manage their events in the console (/console/), never here."""
from django.contrib import admin

from events.models import Device, Event, Location, Notification, ScheduledItem, generate_event_code


class LocationInline(admin.TabularInline):
    model = Location
    extra = 0
    fields = ['name', 'category', 'address', 'latitude', 'longitude', 'hidden']


class NotificationInline(admin.TabularInline):
    model = Notification
    extra = 0
    fields = ['title', 'body', 'send_time', 'priority', 'sent']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    inlines = [LocationInline, NotificationInline]
    list_display = ['name', 'event_code', 'status', 'start_time', 'paid', 'plan', 'user']
    list_filter = ['status', 'paid', 'plan']
    search_fields = ['name', 'event_code', 'address', 'user__email']
    date_hierarchy = 'start_time'
    raw_id_fields = ['user']

    def get_readonly_fields(self, request, obj=None):
        return ['event_code'] if obj is not None else []  # the join code never changes once made

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.event_code:
            obj.event_code = generate_event_code()
        if not obj.pk and obj.user_id is None:
            obj.user = request.user
        super().save_model(request, obj, form, change)


class ScheduledItemInline(admin.TabularInline):
    model = ScheduledItem
    extra = 0
    fields = ['name', 'start_time', 'end_time', 'hidden']
    ordering = ['start_time']


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    inlines = [ScheduledItemInline]
    list_display = ['name', 'event', 'category', 'address']
    list_filter = ['event']
    search_fields = ['name', 'address', 'event__name']
    raw_id_fields = ['event', 'user']

    def save_formset(self, request, form, formset, change):
        for instance in formset.save(commit=False):
            if not instance.pk:
                instance.event = form.instance.event
                instance.user = form.instance.user
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(ScheduledItem)
class ScheduledItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_time', 'location', 'event']
    list_filter = ['event']
    search_fields = ['name', 'description']
    date_hierarchy = 'start_time'
    ordering = ['start_time']
    raw_id_fields = ['event', 'location', 'user']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'event', 'send_time', 'priority', 'sent']
    list_filter = ['sent', 'priority', 'event']
    search_fields = ['title', 'body', 'event__name']
    date_hierarchy = 'send_time'
    raw_id_fields = ['event', 'user']


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['id', 'event', 'platform', 'app_clip', 'subscribed']
    list_filter = ['platform', 'app_clip', 'subscribed']
    search_fields = ['id']
    raw_id_fields = ['event']
