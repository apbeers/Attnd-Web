release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn attnd.wsgi --worker-class gthread --workers 2 --threads 4 --timeout 300
