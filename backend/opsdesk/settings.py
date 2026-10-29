"""OpsDesk settings. Every deploy-time value comes from environment variables so the same
container image runs locally, in staging and in production.

"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_KEY_ID = "k1"
_DEV_KEY_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_DEV_JWT_SECRET = "dev-only-change-me"
KEY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class ConfigurationError(RuntimeError):
    """Raised when required production configuration is missing or insecure."""


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


def _first(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def is_testing() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules or os.environ.get("TESTING") == "1"


def resolve_signing_keys(key_id: str | None, secret: str | None, debug: bool, testing: bool) -> tuple[str, str]:
    """Validate and resolve the tenant-context signing key. Fails closed; never echoes secret material."""
    resolved_id = key_id
    resolved_secret = secret
    relaxed = debug or testing
    if not resolved_id:
        if relaxed:
            resolved_id = _DEV_KEY_ID
        else:
            raise ConfigurationError("OPSDESK_SIGNING_KEY_ID environment variable is required in production.")
    if not resolved_secret:
        if relaxed:
            resolved_secret = _DEV_KEY_SECRET
        else:
            raise ConfigurationError("OPSDESK_SIGNING_KEY_SECRET environment variable is required in production.")
    if not KEY_ID_REGEX.match(resolved_id):
        raise ConfigurationError("OPSDESK_SIGNING_KEY_ID must be 1-32 alphanumeric characters, dashes, or underscores.")
    if len(resolved_secret) != 64 or not re.match(r"^[a-fA-F0-9]{64}$", resolved_secret):
        raise ConfigurationError("OPSDESK_SIGNING_KEY_SECRET must be a 64-character hex string (32 bytes).")
    if not relaxed and resolved_secret.lower() == _DEV_KEY_SECRET.lower():
        raise ConfigurationError("Insecure development signing key secret cannot be used in production.")
    return resolved_id, resolved_secret


@dataclass
class Settings:
    # --- Core -------------------------------------------------------------
    debug: bool = False
    testing: bool = False
    public_base_url: str = "http://localhost:8080"
    allowed_hosts: list[str] = field(default_factory=list)
    cors_allowed_origins: list[str] = field(default_factory=list)
    secure_ssl_redirect: bool = True
    secure_hsts_seconds: int = 31536000
    log_level: str = "INFO"
    docs_enabled: bool = False

    # --- Database ---------------------------------------------------------
    # DATABASE_URL must use the least-privileged *application* role so that PostgreSQL
    # row-level security is enforced. Migrations run as the owner (MIGRATION_DATABASE_URL).
    database_url: str = "postgresql://opsdesk_app:app@localhost:5432/opsdesk"
    migration_database_url: str = ""
    db_app_role: str = "opsdesk_app"
    db_require_ssl: bool = False
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    db_pool_timeout: float = 10.0

    # --- Auth / JWT -------------------------------------------------------
    jwt_secret_key: str = _DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = 15
    jwt_refresh_hours: int = 24
    throttle_login: str = "10/min"
    throttle_anon: str = "60/min"
    throttle_user: str = "1200/min"
    throttle_inbound: str = "600/min"

    # --- Tenant context signing ------------------------------------------
    signing_key_id: str = _DEV_KEY_ID
    signing_key_secret: str = _DEV_KEY_SECRET
    replay_ttl: int = 70
    replay_timeout: float = 2.0

    # --- Redis / jobs -----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    use_local_cache: bool = False
    celery_task_always_eager: bool = False
    celery_concurrency: int = 4

    # --- Secrets encryption ----------------------------------------------
    field_encryption_keys: list[str] = field(default_factory=list)

    # --- Attachments / storage -------------------------------------------
    storage_backend: str = "local"  # local | s3
    media_root: Path = BASE_DIR / "media"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""
    s3_sse: str = "AES256"
    s3_presign_seconds: int = 300
    attachment_max_mb: int = 20
    attachment_blocked_extensions: list[str] = field(default_factory=list)
    attachment_scanner: str = "none"  # none | clamav
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # --- Email ------------------------------------------------------------
    email_host: str = "localhost"
    email_port: int = 1025
    email_host_user: str = ""
    email_host_password: str = ""
    email_use_tls: bool = False
    email_console: bool = False
    default_from_email: str = "OpsDesk <support@example.com>"

    # --- Channels ---------------------------------------------------------
    channel_adapters: dict[str, str] = field(default_factory=dict)

    # --- AI ---------------------------------------------------------------
    ai_embedding_provider: str = "hashing"
    ai_embedding_dim: int = 384  # fixed: changing it requires a migration + reindex
    ai_embedding_model: str = "text-embedding-3-small"
    ai_embedding_base_url: str = "https://api.openai.com/v1"
    ai_embedding_api_key: str = ""
    ai_llm_provider: str = "none"
    ai_llm_model: str = "claude-haiku-5-5"
    ai_llm_base_url: str = ""
    ai_llm_api_key: str = ""
    ai_duplicate_threshold: float = 0.42
    ai_similar_min_score: float = 0.12
    ai_redact_pii: bool = True

    # --- Inbound outbox ---------------------------------------------------
    inbound_max_attempts: int = 5

    @property
    def conninfo(self) -> str:
        """Database URI in a form psycopg understands (``postgres://`` -> ``postgresql://``)."""
        return _psycopg_url(self.database_url)

    @property
    def sqlalchemy_url(self) -> str:
        return _sqlalchemy_url(self.database_url)

    @property
    def migration_sqlalchemy_url(self) -> str:
        return _sqlalchemy_url(self.migration_database_url or self.database_url)

    # Aliases used by the API modules.
    @property
    def redis_timeout(self) -> float:
        return self.replay_timeout

    def get_conninfo(self) -> str:
        return self.conninfo

    def get_signing_config(self) -> tuple[str, bytes]:
        return self.signing_config()

    def signing_config(self) -> tuple[str, bytes]:
        key_id, secret_hex = resolve_signing_keys(
            self.signing_key_id, self.signing_key_secret, self.debug, self.testing
        )
        return key_id, bytes.fromhex(secret_hex)


def _psycopg_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql+psycopg://", "postgresql+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def _sqlalchemy_url(url: str) -> str:
    """Normalise to the psycopg 3 SQLAlchemy dialect."""
    return "postgresql+psycopg://" + _psycopg_url(url)[len("postgresql://"):]


DEFAULT_ADAPTERS = {
    "email": "opsdesk.channels.adapters.email.EmailWebhookAdapter",
    "imap": "opsdesk.channels.adapters.email.ImapAdapter",
    "whatsapp": "opsdesk.channels.adapters.whatsapp.WhatsAppCloudAdapter",
    "webhook": "opsdesk.channels.adapters.webhook.GenericWebhookAdapter",
}


def load_settings() -> Settings:
    debug = env_bool("OPSDESK_DEBUG", False)
    testing = is_testing()
    neural = (env("AI_EMBEDDING_PROVIDER", "hashing") or "hashing") != "hashing"

    key_id, secret = resolve_signing_keys(
        env("OPSDESK_SIGNING_KEY_ID"), env("OPSDESK_SIGNING_KEY_SECRET"), debug, testing
    )

    jwt_key = _first("JWT_SECRET_KEY", "JWT_SIGNING_KEY")
    if not jwt_key:
        if debug or testing:
            jwt_key = _DEV_JWT_SECRET
        else:
            raise ConfigurationError("JWT_SECRET_KEY is required in production.")
    elif jwt_key == _DEV_JWT_SECRET and not (debug or testing):
        raise ConfigurationError("The development JWT secret cannot be used in production.")

    adapters = dict(DEFAULT_ADAPTERS)
    for extra in env_list("EXTRA_CHANNEL_ADAPTERS"):  # "kind=dotted.path.Class"
        kind, _, path = extra.partition("=")
        if kind and path:
            adapters[kind] = path

    return Settings(
        debug=debug,
        testing=testing,
        public_base_url=env("PUBLIC_BASE_URL", "http://localhost:8080") or "",
        allowed_hosts=env_list("OPSDESK_ALLOWED_HOSTS", "localhost,127.0.0.1"),
        cors_allowed_origins=env_list("CORS_ALLOWED_ORIGINS", ""),
        secure_ssl_redirect=(not debug) and env_bool("SECURE_SSL_REDIRECT", True),
        secure_hsts_seconds=env_int("SECURE_HSTS_SECONDS", 31536000),
        log_level=env("LOG_LEVEL", "INFO") or "INFO",
        docs_enabled=env_bool("FASTAPI_DOCS_ENABLED", False),
        database_url=env("DATABASE_URL", "postgresql://opsdesk_app:app@localhost:5432/opsdesk") or "",
        migration_database_url=env("MIGRATION_DATABASE_URL", "") or "",
        db_app_role=env("DB_APP_ROLE", "opsdesk_app") or "opsdesk_app",
        db_require_ssl=env_bool("DB_REQUIRE_SSL", False),
        db_pool_min_size=env_int("FASTAPI_DB_POOL_MIN", 2),
        db_pool_max_size=env_int("FASTAPI_DB_POOL_MAX", 10),
        db_pool_timeout=env_float("FASTAPI_DB_POOL_TIMEOUT", 10.0),
        jwt_secret_key=jwt_key,
        jwt_algorithm=env("JWT_ALGORITHM", "HS256") or "HS256",
        jwt_access_minutes=env_int("JWT_ACCESS_MINUTES", 15),
        jwt_refresh_hours=env_int("JWT_REFRESH_HOURS", 24),
        throttle_login=env("THROTTLE_LOGIN", "10/min") or "10/min",
        throttle_anon=env("THROTTLE_ANON", "60/min") or "60/min",
        throttle_user=env("THROTTLE_USER", "1200/min") or "1200/min",
        throttle_inbound=env("THROTTLE_INBOUND", "600/min") or "600/min",
        signing_key_id=key_id,
        signing_key_secret=secret,
        replay_ttl=env_int("OPSDESK_REPLAY_TTL", 70),
        replay_timeout=env_float("OPSDESK_REPLAY_TIMEOUT", 2.0),
        redis_url=env("REDIS_URL", "redis://localhost:6379/0") or "",
        use_local_cache=env_bool("USE_LOCAL_CACHE", False),
        celery_task_always_eager=env_bool("CELERY_TASK_ALWAYS_EAGER", False),
        celery_concurrency=env_int("CELERY_CONCURRENCY", 4),
        field_encryption_keys=env_list("FIELD_ENCRYPTION_KEYS", ""),
        storage_backend=env("STORAGE_BACKEND", "local") or "local",
        media_root=Path(env("MEDIA_ROOT", str(BASE_DIR / "media")) or ""),
        s3_bucket=env("S3_BUCKET", "") or "",
        s3_region=env("S3_REGION", "") or "",
        s3_endpoint_url=env("S3_ENDPOINT_URL", "") or "",
        s3_sse=env("S3_SSE", "AES256") or "AES256",
        attachment_max_mb=env_int("ATTACHMENT_MAX_MB", 20),
        attachment_blocked_extensions=env_list(
            "ATTACHMENT_BLOCKED_EXTENSIONS", ".exe,.bat,.cmd,.com,.scr,.js,.vbs,.ps1,.msi,.jar,.dll"
        ),
        attachment_scanner=env("ATTACHMENT_SCANNER", "none") or "none",
        clamav_host=env("CLAMAV_HOST", "clamav") or "clamav",
        clamav_port=env_int("CLAMAV_PORT", 3310),
        email_host=env("EMAIL_HOST", "localhost") or "localhost",
        email_port=env_int("EMAIL_PORT", 1025),
        email_host_user=env("EMAIL_HOST_USER", "") or "",
        email_host_password=env("EMAIL_HOST_PASSWORD", "") or "",
        email_use_tls=env_bool("EMAIL_USE_TLS", False),
        email_console="console" in (env("EMAIL_BACKEND", "") or ""),
        default_from_email=env("DEFAULT_FROM_EMAIL", "OpsDesk <support@example.com>") or "",
        channel_adapters=adapters,
        ai_embedding_provider=env("AI_EMBEDDING_PROVIDER", "hashing") or "hashing",
        ai_embedding_model=env("AI_EMBEDDING_MODEL", "text-embedding-3-small") or "",
        ai_embedding_base_url=env("AI_EMBEDDING_BASE_URL", "https://api.openai.com/v1") or "",
        ai_embedding_api_key=env("AI_EMBEDDING_API_KEY", "") or "",
        ai_llm_provider=env("AI_LLM_PROVIDER", "none") or "none",
        ai_llm_model=env("AI_LLM_MODEL", "claude-haiku-5-5") or "",
        ai_llm_base_url=env("AI_LLM_BASE_URL", "") or "",
        ai_llm_api_key=env("AI_LLM_API_KEY", "") or "",
        # Thresholds depend on the embedder: the local hashing embedder produces lower absolute
        # cosine scores than neural embeddings for the same pair.
        ai_duplicate_threshold=env_float("AI_DUPLICATE_THRESHOLD", 0.82 if neural else 0.42),
        ai_similar_min_score=env_float("AI_SIMILAR_MIN_SCORE", 0.35 if neural else 0.12),
        ai_redact_pii=env_bool("AI_REDACT_PII", True),
        inbound_max_attempts=env_int("INBOUND_MAX_ATTEMPTS", 5),
    )


settings = load_settings()
