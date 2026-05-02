from django.db import connection, OperationalError
from django.http import JsonResponse


def health(request):
    """
    ALB health check endpoint. Returns 200 when the app and DB are reachable,
    503 when the DB is down (so ALB stops routing traffic to the task).
    """
    db_ok = True
    try:
        connection.ensure_connection()
    except OperationalError:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status)
