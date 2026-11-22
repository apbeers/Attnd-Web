"""Django's WSGI application served from gunicorn's ASGI worker, in a fixed pool of threads (batch 7, Track C).

Why not Django's own ASGI handler: with our synchronous middleware it starts a new thread for every request
(asgiref's ThreadSensitiveContext); measured locally that cut /health/ from ~820 to ~290 requests a second and the
DEMO bundle from ~139 to ~106. This bridge keeps exactly today's WSGI stack (same middleware, same views, same
`request.environ`) on a reused pool of WEB_THREADS threads, like gthread did, while the event loop in front of it holds
long-polls without a thread (attnd/longpoll_asgi.py). asgiref's `WsgiToAsgi` isn't used: its calls are
thread-sensitive, so every request would queue on one thread.

Request bodies are read on the event loop first (Django's DATA_UPLOAD_MAX_MEMORY_SIZE and our views' own limits still
apply), spooled to disk past 1 MB; responses are sent chunk by chunk from the worker thread, so streaming responses
(the diagnostics export) still stream.
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from tempfile import SpooledTemporaryFile

from asgiref.wsgi import WsgiToAsgiInstance

THREADS = max(1, int(os.getenv('WEB_THREADS', '4') or 4))


class WsgiBridge:
    def __init__(self, wsgi_application, threads=THREADS):
        self.wsgi = wsgi_application
        self.pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix='web')

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':  # Django has no startup / shutdown hooks
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        if scope['type'] != 'http':
            return  # no websockets
        body = SpooledTemporaryFile(max_size=1024 * 1024)
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                body.close()
                return
            body.write(message.get('body', b''))
            if not message.get('more_body'):
                break
        body.seek(0)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self.pool, self._run, scope, body, send, loop)
        finally:
            body.close()

    def _run(self, scope, body, send, loop):
        def push(message):
            asyncio.run_coroutine_threadsafe(send(message), loop).result()

        adapter = WsgiToAsgiInstance(self.wsgi)
        adapter.scope = scope  # build_environ reads the headers from it
        environ = adapter.build_environ(scope, body)
        started = {}

        def start_response(status, headers, exc_info=None):
            if exc_info and started.get('sent'):
                raise exc_info[1].with_traceback(exc_info[2])
            started['status'] = int(status.split(' ', 1)[0])
            started['headers'] = [(k.lower().encode('latin-1'), v.encode('latin-1')) for k, v in headers]
            return lambda data: None  # the legacy write() callable: Django never uses it

        result = self.wsgi(environ, start_response)
        try:
            pending = None
            for chunk in result:
                if not started.get('sent'):
                    push({'type': 'http.response.start', 'status': started['status'], 'headers': started['headers']})
                    started['sent'] = True
                if pending is not None:
                    push({'type': 'http.response.body', 'body': pending, 'more_body': True})
                pending = chunk
            if not started.get('sent'):
                push({'type': 'http.response.start', 'status': started['status'], 'headers': started['headers']})
            push({'type': 'http.response.body', 'body': pending or b'', 'more_body': False})
        finally:
            if hasattr(result, 'close'):
                result.close()  # request_finished: database connections go back to the pool
