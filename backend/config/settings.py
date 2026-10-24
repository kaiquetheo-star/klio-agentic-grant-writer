"""Minimal Django settings — Reasoning Engine submission only.

No Klio billing/CRM/auth/WhatsApp. SQLite by default. SAMPLE_MODE works offline.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "submission-dev-only-change-me-not-for-production",
)
DEBUG = os.environ.get("DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,*").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.reasoning_engine.apps.ReasoningEngineConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# SQLite only for this public submission (never production Postgres).
_database_url = (os.environ.get("DATABASE_URL") or "").strip()
if _database_url.startswith("sqlite:////"):
    # Absolute: sqlite:////tmp/file.sqlite3 → /tmp/file.sqlite3
    _db_name = "/" + _database_url[len("sqlite:////") :]
elif _database_url.startswith("sqlite:///"):
    # Relative to CWD: sqlite:///relative.db
    _db_name = _database_url[len("sqlite:///") :]
else:
    _db_name = os.environ.get(
        "SQLITE_PATH", str(REPO_ROOT / "tmp" / "submission.sqlite3")
    )

_db_path = Path(_db_name)
_db_path.parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_db_path),
    }
}

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "UNAUTHENTICATED_USER": None,
}

REASONING_ENGINE_ENABLED = os.environ.get(
    "REASONING_ENGINE_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
REASONING_ENGINE_MAX_REVISIONS = int(
    os.environ.get("REASONING_ENGINE_MAX_REVISIONS", "3") or "3"
)
REASONING_ENGINE_TARGET_SCORE = float(
    os.environ.get("REASONING_ENGINE_TARGET_SCORE", "8.0") or "8.0"
)
