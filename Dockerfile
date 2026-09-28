# The Woodlands IMS as one container: any host that runs containers can run it
# (Render, Railway, Fly, DigitalOcean App Platform...). The host provides the
# PostgreSQL database and HTTPS; this image provides everything else.
#
# Required on the host:  DJANGO_SECRET_KEY, DATABASE_URL, DJANGO_ALLOWED_HOSTS,
#                        DJANGO_CSRF_TRUSTED_ORIGINS
# Set here, and not to be changed on a server: DJANGO_DEBUG=0, DJANGO_HTTPS=1.
# See docs/runbook.md, "Deployment".

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=0 \
    DJANGO_HTTPS=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

COPY . .

# The CSS is gathered and compressed once, at build time. The key used here
# only exists for this one command and is never the server's key.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 woodlands \
    && chown -R woodlands /app
USER woodlands

EXPOSE 8000

# Migrations run on every start: a new version brings its own database
# changes with it, and a migration that fails stops the start rather than
# leaving the app running against the wrong tables.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT} --workers 3 --timeout 60 --access-logfile -"]
