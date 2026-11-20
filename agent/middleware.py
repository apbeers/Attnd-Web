from django.contrib.sessions.middleware import SessionMiddleware as DjangoSessionMiddleware


class SessionMiddleware(DjangoSessionMiddleware):
    """Django's session middleware, plus Ask Attnd's internal requests (agent/internal.py).

    An internal request carries ``agent_session``: the signed-in person's own session, loaded read-only, so the
    authentication, two-factor and permission checks are exactly theirs (signing out ends the agent's access too).
    It is never saved from an internal request, so the agent can't change the person's session. The attribute only
    exists on request objects built in this process; nothing from HTTP can set it.
    """

    def process_request(self, request):
        session = getattr(request, 'agent_session', None)
        if session is not None:
            request.session = session
            return
        super().process_request(request)

    def process_response(self, request, response):
        if getattr(request, 'agent_session', None) is not None:
            return response
        return super().process_response(request, response)
