release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn attnd.wsgi --timeout 120
