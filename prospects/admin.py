from django.contrib import admin

from prospects.models import Prospect, ProspectContact, ProspectRun, ProspectSettings


class ContactInline(admin.TabularInline):
    model = ProspectContact
    extra = 0


@admin.register(Prospect)
class ProspectAdmin(admin.ModelAdmin):
    list_display = ('name', 'organizer', 'city', 'start_date', 'fit_score', 'status')
    list_filter = ('status',)
    search_fields = ('name', 'organizer', 'city')
    inlines = [ContactInline]
    raw_id_fields = ('event', 'ingest_job', 'first_seen_run', 'last_seen_run')


@admin.register(ProspectRun)
class ProspectRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'trigger', 'status', 'created_at', 'new', 'cost_usd')


admin.site.register(ProspectSettings)
