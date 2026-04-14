#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
until python -c "import os, psycopg; psycopg.connect(host=os.getenv('POSTGRES_HOST', 'db'), port=os.getenv('POSTGRES_PORT', '5432'), dbname=os.getenv('POSTGRES_DB', 'credito_digital'), user=os.getenv('POSTGRES_USER', 'credito_digital'), password=os.getenv('POSTGRES_PASSWORD', ''))"
do
  sleep 2
done

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn core.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --timeout 120
