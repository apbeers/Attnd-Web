release: python manage.py migrate --noinput && python manage.py collectstatic --noinput && python manage.py seed_test_data
web: gunicorn attnd.wsgi --worker-class gthread --workers 1 --threads 4 --timeout 300
worker: python manage.py process_ingest_jobs
