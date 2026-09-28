"""
Django settings for the Sofreh reservation watcher.

Secrets (Django secret key, Samad tokens, Telegram bot token) are read from the
environment / a local .env file and are never committed. Runtime configuration
that the user edits day to day lives in the database instead — see sofreh.models.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=()):
    raw = os.environ.get(name, "")
    values = [item.strip() for item in raw.replace(",", " ").split()]
    return values or list(default)


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-in-.env"
)
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "sofreh",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "samad_reporter.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "samad_reporter.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("SQLITE_NAME", "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sofreh -----------------------------------------------------------------
# Endpoints and knobs for the Samad dining system. Only change these if the
# university moves the service; per-user settings belong in the admin.
SOFREH = {
    "API_BASE_URL": os.environ.get(
        "SAMAD_BASE_URL", "https://setad.dining.sharif.edu"
    ),
    "PROGRAMS_PATH": "/rest/reservations/programs/v2",
    "TOKEN_PATH": "/oauth/token",
    # The mobile app's OAuth client. A secret is only needed if the server
    # demands HTTP Basic auth on the token endpoint.
    "OAUTH_CLIENT_ID": os.environ.get("SAMAD_CLIENT_ID", "samad-mobile"),
    "OAUTH_CLIENT_SECRET": os.environ.get("SAMAD_CLIENT_SECRET", ""),
    "REQUEST_TIMEOUT": int(os.environ.get("SAMAD_TIMEOUT", "20")),
    "USER_AGENT": os.environ.get("SAMAD_USER_AGENT", "Sofreh/0.1 (+reservation-watcher)"),
    # How long `manage.py watch` sleeps between polls, in seconds.
    "POLL_INTERVAL_SECONDS": int(os.environ.get("SOFREH_POLL_INTERVAL", "300")),
    # Warn on Telegram this many days before the refresh token expires. Nothing
    # can renew that token, so the warning has to arrive while there is still
    # time to log in and repaste.
    "TOKEN_EXPIRY_WARNING_DAYS": int(
        os.environ.get("SOFREH_EXPIRY_WARNING_DAYS", "14")
    ),
    # A gated watch (one that `follows` another) is normally skipped until the
    # watch it follows opens. This is the safety net: poll it anyway if it has
    # not been checked for this long, so a self that publishes first can never
    # be missed entirely. Set to 0 to trust the gate completely.
    "GATED_RECHECK_MINUTES": int(os.environ.get("SOFREH_GATED_RECHECK", "60")),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "sofreh": {
            "handlers": ["console"],
            "level": os.environ.get("SOFREH_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
