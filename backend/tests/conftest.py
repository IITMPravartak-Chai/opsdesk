"""Test infrastructure: a real PostgreSQL (pgvector) database built by Alembic, Redis, and factories.

Environment (defaults suit the local docker containers described in the README):

    OPSDESK_TEST_ADMIN_URL   superuser URL used to (re)create the throwaway test database
    OPSDESK_TEST_REDIS_URL   Redis used for login throttling (database 15 is flushed)

The tests never touch any database but ``opsdesk_test``. Row-level security is exercised for real: the application connects
as the restricted ``opsdesk_app`` role and migrations run as ``opsdesk_owner``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import psycopg

ADMIN_URL = os.environ.get("OPSDESK_TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:55432/postgres")
REDIS_URL = os.environ.get("OPSDESK_TEST_REDIS_URL", "redis://localhost:56379/15")
TEST_DB = "opsdesk_test"
APP_PW, OWNER_PW = "CHANGE_ME_app", "CHANGE_ME_owner"
_host = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]

os.environ.update({
    "OPSDESK_DEBUG": "1", "OPSDESK_PBKDF2_ITERATIONS": "1000", "USE_LOCAL_CACHE": "1", "CELERY_TASK_ALWAYS_EAGER": "1", "EMAIL_BACKEND": "console",
    "DATABASE_URL": f"postgresql://opsdesk_app:{APP_PW}@{_host}/{TEST_DB}",
    "MIGRATION_DATABASE_URL": f"postgresql://opsdesk_owner:{OWNER_PW}@{_host}/{TEST_DB}",
    "REDIS_URL": REDIS_URL, "TESTING": "1", "FASTAPI_DB_POOL_MIN": "1", "FASTAPI_DB_POOL_MAX": "4",
    "MEDIA_ROOT": os.path.join(os.path.dirname(__file__), ".media"),
})
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402


def _create_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}", autocommit=True) as c:
        c.execute("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'opsdesk_owner') THEN
                CREATE ROLE opsdesk_owner LOGIN PASSWORD 'CHANGE_ME_owner'; END IF;
              IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'opsdesk_app') THEN
                CREATE ROLE opsdesk_app LOGIN PASSWORD 'CHANGE_ME_app'; END IF;
            END $$""")
        for ext in ("vector", "pg_trgm"):
            c.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        c.execute(f"GRANT CONNECT ON DATABASE {TEST_DB} TO opsdesk_owner, opsdesk_app")
        c.execute(f"GRANT CREATE ON DATABASE {TEST_DB} TO opsdesk_owner")
        c.execute("GRANT ALL ON SCHEMA public TO opsdesk_owner")
        c.execute("ALTER SCHEMA public OWNER TO opsdesk_owner")
        c.execute("GRANT USAGE ON SCHEMA public TO opsdesk_app")


@pytest.fixture(scope="session", autouse=True)
def database():
    """Fresh schema from the Alembic migrations (the same path a new deployment takes)."""
    _create_database()
    from opsdesk.cli import BACKEND_DIR
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    yield TEST_DB
    from opsdesk.db import session as sess
    sess.dispose_engines()


@pytest.fixture(autouse=True)
def single_super_admin_per_test():
    """The site has at most one super admin (unique index). Tests build several worlds, so demote it afterwards."""
    yield
    from opsdesk.db.session import get_owner_engine
    with get_owner_engine().begin() as c:  # the owner is not bound by the tenant row-level-security policy
        c.exec_driver_sql("UPDATE core_user SET role = 'manager' WHERE role = 'super_admin'")


@pytest.fixture
def wide_admin_access(monkeypatch):
    """Administration is the super admin's alone, and only one super admin can exist on the site. Tests of the admin
    routers' own rules (who may edit whom, validation, audit) run many tenants at once, so they open the guard to the
    old manager-level roles. test_administration_access.py tests the real guard."""
    from opsdesk.api import security
    from opsdesk.api.routers import channels
    from opsdesk.db.models.core import PRIVILEGED_ROLES
    monkeypatch.setattr(security, "ADMIN_ROLES", PRIVILEGED_ROLES)
    monkeypatch.setattr(channels, "ADMIN_ROLES", PRIVILEGED_ROLES)


@pytest.fixture(autouse=True)
def reset_throttle_state():
    """Replay slots (local store) and sign-in throttle counters are per-test state."""
    import redis

    from opsdesk import tenancy
    tenancy.reset_local_replay_store()
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        r.flushdb()
    except Exception:  # noqa: BLE001 - tests that need Redis are skipped by their own fixture
        pass
    yield


# ---------------------------------------------------------------------------------------------- factories
class World:
    """A tenant with the default ITIL configuration and one user per role, created through the real services."""

    PASSWORD = "Xk9!vQ2#mLw8pZ-test"

    def __init__(self, tenant_id: int, slug: str):
        self.tenant_id, self.slug = tenant_id, slug
        self.users: dict[str, int] = {}
        self.emails: dict[str, str] = {}

    def email(self, role: str) -> str:
        return self.emails[role]


def make_world(name: str | None = None, *, roles=("manager", "team_lead", "employee", "customer"),
               default_config: bool = True) -> World:
    from opsdesk.db.models import Account, Tenant, User
    from opsdesk.db.session import plain_session, tenant_session
    from opsdesk.security.passwords import make_password
    from opsdesk.services.bootstrap import install_defaults

    tag = uuid.uuid4().hex[:8]
    with plain_session() as ps:
        t = Tenant(name=name or f"Tenant {tag}", slug=f"t-{tag}", timezone="UTC", settings={})
        ps.add(t)
        ps.flush()
        tid, slug = t.id, t.slug
    w = World(tid, slug)
    with tenant_session(tid) as s:
        if default_config:
            install_defaults(s, s.get(Tenant, tid))
        acct = Account(tenant_id=tid, name="Globex", code="GLBX", tier="gold", email_domains=["globex.example"], notes="")
        s.add(acct)
        s.flush()
        for role in roles:
            email = f"{role}-{tag}@tests.example"
            u = User(email=email, username=email, tenant_id=tid, role=role, first_name=role.title(), last_name="Tester",
                     password=make_password(World.PASSWORD), is_staff=role in ("manager", "team_lead"),
                     account_id=acct.id if role == "customer" else None)
            s.add(u)
            s.flush()
            w.users[role], w.emails[role] = u.id, email
        w.account_id = acct.id  # type: ignore[attr-defined]
    return w


@pytest.fixture
def world() -> World:
    return make_world()


@pytest.fixture
def other_world() -> World:
    return make_world()


@pytest.fixture
def session_for(world):
    """Open a tenant-bound session: ``with session_for(world) as s``."""
    from opsdesk.db.session import tenant_session

    def _open(w=None, **kw):
        return tenant_session((w or world).tenant_id, **kw)
    return _open


@pytest.fixture(scope="session")
def api():
    from fastapi.testclient import TestClient

    from opsdesk.api.main import app
    with TestClient(app) as c:
        yield c


def login(api, w: World, role: str) -> dict:
    r = api.post("/api/auth/login/", json={"email": w.email(role), "password": World.PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


@pytest.fixture
def auth(api, world):
    """Headers by role for ``world``: ``auth('employee')``."""
    cache: dict[str, dict] = {}

    def _auth(role: str, w: World | None = None) -> dict:
        w = w or world
        key = f"{w.tenant_id}:{role}"
        if key not in cache:
            cache[key] = login(api, w, role)
        return cache[key]
    return _auth


def ticket_type_id(api, headers, key="incident") -> int:
    meta = api.get("/api/meta/", headers=headers).json()
    return next(t["id"] for t in meta["ticket_types"] if t["key"] == key)


def assign_to_employee(api, world, auth, ticket) -> dict:
    """Employees only see tickets assigned to or raised by them, so a test that has the employee work a ticket assigns it first."""
    r = api.patch(f"/api/tickets/{ticket['id']}/", headers=auth("manager"), json={"assignee": world.users["employee"]})
    assert r.status_code == 200, r.text
    return ticket


def create_ticket_api(api, headers, subject="Printer is offline", type_key="incident", **extra) -> dict:
    body = {"ticket_type": ticket_type_id(api, headers, type_key), "subject": subject,
            "description": extra.pop("description", "It stopped working this morning"), **extra}
    r = api.post("/api/tickets/", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()
