"""
Django settings for the BGU IMKN applicant system.

Security-related choices are documented inline because they are part of the
coursework requirements (password hashing, 2FA, session hardening, headers).
"""
from pathlib import Path

import os

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    # A fixed development-only key. Production must always set DJANGO_SECRET_KEY.
    SECRET_KEY = "django-insecure-dev-only-key-do-not-use-in-production-000000"

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")

# Reverse proxy (nginx) trust for X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("DJANGO_BEHIND_HTTPS") else None

# HTTPS hardening: off in development so that plain http://localhost keeps working.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "accounts",
    "events",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.SessionSecurityMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "accounts.context_processors.nav_flags",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database: PostgreSQL, as required by the term paper specification -------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "bgu_applicants"),
        "USER": os.environ.get("POSTGRES_USER", "bgu_app"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "ATOMIC_REQUESTS": False,
    }
}

AUTH_USER_MODEL = "accounts.User"

# --- Password hashing: Argon2id first, PBKDF2 kept as a fallback -------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Sessions ----------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "bgu_sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # needed only if JS sends the token; kept explicit
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", "1800"))  # 30 minutes of idle time

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Asia/Irkutsk"  # Ulan-Ude (Buryat State University)
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "events:catalog"
LOGOUT_REDIRECT_URL = "accounts:login"

# --- Two-factor authentication (TOTP: Yandex.Key, Google Authenticator, ...) -
TOTP_ISSUER = os.environ.get("TOTP_ISSUER", "БГУ ИМиКН")
TOTP_DIGITS = 6
TOTP_PERIOD = 30
# Accept one step back/forward to tolerate clock drift in the authenticator app.
TOTP_VALID_WINDOW = 1
TOTP_RECOVERY_CODES = 8
# Reused/failed second factors are rate limited per account.
TWOFA_MAX_ATTEMPTS = int(os.environ.get("TWOFA_MAX_ATTEMPTS", "5"))
TWOFA_LOCKOUT_SECONDS = int(os.environ.get("TWOFA_LOCKOUT_SECONDS", "300"))

# --- MAX messenger bot -------------------------------------------------------
MAX_API_BASE = os.environ.get("MAX_API_BASE", "https://platform-api2.max.ru")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
# Public nickname of the bot, shown in the "link your chat" instructions.
MAX_BOT_USERNAME = os.environ.get("MAX_BOT_USERNAME", "bgu_imkn_bot")
# Shared secret echoed by MAX in the X-Max-Bot-Api-Secret webhook header.
MAX_WEBHOOK_SECRET = os.environ.get("MAX_WEBHOOK_SECRET", "")
# Without a token the bot is disabled and the UI degrades gracefully.
MAX_ENABLED = bool(MAX_BOT_TOKEN)
# Use MAX as an alternative second factor (one-time code in the chat).
MAX_AS_SECOND_FACTOR = env_bool("MAX_AS_SECOND_FACTOR", True)
MAX_CODE_TTL_SECONDS = 300

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "accounts": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "events": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
