from django.contrib import admin

from scrape.models import Change, ChangeSet, IngestJob


class SuperuserOnly:
    def has_module_permission(self, request):
        return request.user.is_superuser


@admin.register(IngestJob)
class IngestJobAdmin(SuperuserOnly, admin.ModelAdmin):
    list_display = ['created_at', 'kind', 'trigger', 'status', 'event', 'created_by']
    list_filter = ['status', 'kind', 'trigger']
    exclude = ['upload_data']
    readonly_fields = ['result', 'error']


class ChangeInline(admin.TabularInline):
    model = Change
    extra = 0
    fields = ['entity', 'op', 'label', 'status', 'minor', 'conflict_fields']
    readonly_fields = fields


@admin.register(ChangeSet)
class ChangeSetAdmin(SuperuserOnly, admin.ModelAdmin):
    list_display = ['created_at', 'event', 'status', 'summary']
    list_filter = ['status']
    inlines = [ChangeInline]
