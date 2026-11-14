from django.db import connection
from django.http import JsonResponse


def health(request):
    """Unauthenticated liveness check used by the deploy pipeline."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        db = 'ok'
    except Exception:
        db = 'error'
    status = 200 if db == 'ok' else 503
    return JsonResponse({'status': 'ok' if status == 200 else 'degraded', 'database': db}, status=status)
