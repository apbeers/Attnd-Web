"""The tool list Ask Attnd offers the model, built once from the registry.

One definition, many runners: the in-app chat's loop (agent/loop.py) and the MCP server (mcp/, phase 3) both take
their tools from here, so a tool is offered by the same rules everywhere (who may run it, the area it belongs to,
whether it is always loaded). Nothing in this module runs a tool; runners do that through agent/internal.py.
"""

from agent import registry

FIXED_TOOLS = [
    {'name': 'read_page', 'description': 'Open a console page as the person and read it as text (headings, values, '
                                         'tables, links with ids). Long pages continue with offset.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'offset'], 'properties': {
         'path': {'type': 'string', 'description': 'a console path, e.g. /console/events/12/schedule/'},
         'offset': {'type': ['integer', 'null'], 'description': 'continue a long page from this character'}}},
     'strict': True},
    {'name': 'list_pages', 'description': 'The console pages you can open (for one event, or all).',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['event_id'], 'properties': {
         'event_id': {'type': ['integer', 'null'], 'description': 'only this event\'s pages'}}}, 'strict': True},
    {'name': 'list_events', 'description': 'The person\'s events (upcoming and past) with ids and codes.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': [], 'properties': {}},
     'strict': True},
    {'name': 'search_events', 'description': 'Find the person\'s events by name or code.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['query'], 'properties': {
         'query': {'type': 'string'}}}, 'strict': True},
    {'name': 'ask_user', 'description': 'Ask the person one short question and wait for the answer. Only when it '
                                        'is truly ambiguous; pass the possible answers as options (tappable chips).',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['question', 'options'],
                    'properties': {
                        'question': {'type': 'string'},
                        'options': {'type': ['array', 'null'], 'items': {'type': 'string'},
                                    'description': '2-6 short answers the person can tap (e.g. the event days), or '
                                                   'null for an open question'}}}, 'strict': True},
    {'name': 'platform_help', 'description': 'How-to answers about Attnd (accounts, passwords, passkeys, team, '
                                             'publishing, notifications, QR codes, imports, layout, history, Studio, '
                                             'billing) with the exact console page links to give.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': [], 'properties': {}},
     'strict': True},
    {'name': 'remember', 'description': 'Remember a lasting preference or correction of the person\'s for next time '
                                        '("always call it Main Stage", "notifications short and friendly", "24-hour '
                                        'times", "default category Food"). Only when they say it should apply from '
                                        'now on, or correct you twice. Not facts about the event (those go in the '
                                        'event), never secrets.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['note', 'event_id'], 'properties': {
         'note': {'type': 'string', 'description': 'one short sentence, e.g. "Call the big stage Main Stage"'},
         'event_id': {'type': ['integer', 'null'], 'description': 'only for this event, or null for every event'}}},
     'strict': True},
    {'name': 'finish', 'description': 'End the run with a short summary for the person, and 2-4 follow-ups they '
                                      'might tap next.',
     'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'suggest'],
                    'properties': {
                        'summary': {'type': 'string'},
                        'suggest': {'type': ['array', 'null'], 'items': {'type': 'string'},
                                    'description': '2-4 short follow-up requests in the person\'s words (e.g. "Show '
                                                   'the schedule", "Add a location", "Send a notification"), or null'}}},
     'strict': True},
]


ALWAYS_LOADED = {'design_website'}
AREAS = {'studio': ('studio', 'website', ' site', 'hero', 'homepage', 'web page', 'landing page', 'sponsors page'), 'prospects': ('prospect',),
         'diagnostics': ('diagnostic', 'crash', ' log')}


def _areas_for(run):
    if run is None:
        return set(AREAS)
    text = f' {(run.goal or "").lower()} '
    page = run.page_url or ''
    return {area for area, words in AREAS.items() if f'/{area}/' in page or any(w in text for w in words)}


def tools_for(user, run=None):
    from agent import components
    areas = _areas_for(run)
    writes = [a for a in registry.writes_for(user).values()
              if (a.url_name or '').split(':')[0] not in AREAS or (a.url_name or '').split(':')[0] in areas
              or a.name in ALWAYS_LOADED]
    from agent.proposals import TOOL as PROPOSE_TOOL
    from agent.search_tool import TOOL as SEARCH_TOOL
    return FIXED_TOOLS + [PROPOSE_TOOL, SEARCH_TOOL] + components.tool_definitions(user) + [registry.tool_definition(a) for a in writes]


# ---------------------------------------------------------------------------
# MCP clients (mcp_server/): the same tools, chosen on purpose
# ---------------------------------------------------------------------------

# Ask Attnd's own reads that make sense without the chat (no ask_user / finish / show / fill_form / remember /
# propose_changes: those talk to the person in the chat).
MCP_FIXED = ('list_events', 'search_events', 'read_page', 'list_pages', 'platform_help')
MCP_COMPONENT_TOOLS = ('get_component',)


def mcp_tools_for(user):
    """The tool definitions an MCP client may call as this person: the chat's reads above, the full-text search,
    component data, and the registry's writes marked ``mcp=True`` that this person may run."""
    from agent import components
    from agent.search_tool import TOOL as SEARCH_TOOL
    fixed = [t for t in FIXED_TOOLS if t['name'] in MCP_FIXED]
    comps = [t for t in components.tool_definitions(user) if t['name'] in MCP_COMPONENT_TOOLS]
    writes = [registry.tool_definition(a) for a in registry.writes_for(user).values() if a.mcp]
    return fixed + [SEARCH_TOOL] + comps + writes


def run_tool(run, name, args, call_id):
    """Run one tool for a runner without a model (MCP): exactly the chat's dispatcher (agent/loop.py), so steps,
    permissions, history and gates are the same. Returns (text, waiting step or None). An always-ask action doesn't
    run: its step waits for the person's Confirm (a button step, which runs without the model once confirmed)."""
    from types import SimpleNamespace

    from agent import loop
    from agent.models import AgentRun, AgentStep
    call = SimpleNamespace(name=name, args=args if isinstance(args, dict) else {}, id=call_id)
    try:
        return loop._call(run, call), None
    except loop.Pause as pause:
        step = pause.step
        if step.confirm_state != AgentStep.CONFIRM_PENDING:
            return 'That needs a reply in the chat; MCP clients can\'t answer it.', None
        step.kind = AgentStep.KIND_BUTTON  # confirmed through loop.confirm → _run_button, no model to resume
        step.save(update_fields=['kind'])
        AgentRun.objects.filter(pk=run.pk).update(status=AgentRun.WAITING_CONFIRMATION)
        return step.confirm_summary, step
    except loop.Finished as done:
        return done.summary, None
