# Public hackathon submission — SAMPLE_MODE offline by default.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PYTHONPATH=/app/backend \
    SAMPLE_MODE=true \
    REASONING_ENGINE_ENABLED=true \
    REASONING_ENGINE_MEMORY_BACKEND=memory \
    DEBUG=true \
    ALLOWED_HOSTS=* \
    DJANGO_SECRET_KEY=submission-docker-dev-only-not-for-production \
    DATABASE_URL=sqlite:////tmp/klio-reasoning-submission.sqlite3

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY . /app

WORKDIR /app/backend
EXPOSE 8002

CMD ["sh", "-c", "python manage.py migrate --run-syncdb && python manage.py runserver 0.0.0.0:8002"]
