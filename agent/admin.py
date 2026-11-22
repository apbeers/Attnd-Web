from django.contrib import admin

from agent.models import AgentRun, AgentStep, AgentUsage


class StepInline(admin.TabularInline):
    model = AgentStep
    extra = 0
    fields = ('seq', 'kind', 'tool', 'text', 'ok', 'changed', 'confirm_state', 'url')
    readonly_fields = fields
    can_delete = False


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'goal', 'feedback', 'cost_usd', 'step_count', 'created_at')
    list_filter = ('status', 'feedback')
    search_fields = ('goal', 'user__email')
    exclude = ('session_key', 'state')  # the person's session key never shows
    readonly_fields = ('user', 'event', 'goal', 'page_url', 'status', 'provider', 'model', 'cost_usd', 'input_tokens',
                       'output_tokens', 'step_count', 'summary', 'error', 'revisions', 'created_at', 'finished_at',
                       'feedback', 'feedback_note', 'feedback_at')
    inlines = [StepInline]


@admin.register(AgentUsage)
class AgentUsageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'model', 'input_tokens', 'cached_tokens', 'output_tokens', 'cost_usd')
    readonly_fields = [f.name for f in AgentUsage._meta.fields]
