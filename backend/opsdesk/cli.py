"""Operations CLI: ``python -m opsdesk <command>`` or the ``opsdesk`` entry script.

    migrate               apply database migrations (as the schema owner), then refresh RLS policies and grants
    apply-rls             re-apply row-level security policies and app-role grants (run after adding tenant tables)
    create-tenant         create a tenant with the default ITIL configuration and its first administrator
    create-user           create a user in a tenant (the web console deliberately cannot create users)
    reset-password        set a new password for a user
    seed-demo [--reset]   load the demo tenant
    reindex-embeddings    re-embed tickets and KB articles (after changing the embedding provider/model)
    tenants list|set|setting   inspect and change tenants (platform operators)
    keys list|add|retire  manage the signed tenant-context signing keys
    check                 verify configuration, database and Redis connectivity
"""
from __future__ import annotations

import getpass
import json
import secrets
import sys
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
tenants_app = typer.Typer(help="Inspect and change tenants.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage signing keys for the signed tenant context.", no_args_is_help=True)
app.add_typer(tenants_app, name="tenants")
app.add_typer(keys_app, name="keys")

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _fail(message: str, code: int = 1):
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _password(value: Optional[str], prompt: str = "Password") -> str:
    return value or getpass.getpass(f"{prompt}: ")


def _check_password(pw: str, **who) -> None:
    from .security.passwords import validate_password
    problems = validate_password(pw, **who)
    if problems:
        _fail("Password rejected: " + " ".join(problems))


@app.command()
def migrate(revision: str = typer.Argument("head")) -> None:
    """Apply migrations as the schema owner, then re-apply RLS policies and grants for the app role."""
    from alembic import command
    from alembic.config import Config

    from .db import rls
    from .db.session import get_owner_engine

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, revision)
    with get_owner_engine().begin() as conn:
        n = rls.apply(conn)
    typer.secho(f"Migrated to {revision}; RLS refreshed on {n} relations.", fg=typer.colors.GREEN)


@app.command("apply-rls")
def apply_rls() -> None:
    """Re-apply row-level security policies and app-role grants. Run as the DB owner (MIGRATION_DATABASE_URL)."""
    from .db import rls
    from .db.session import get_owner_engine

    with get_owner_engine().begin() as conn:
        n = rls.apply(conn)
    typer.secho(f"RLS applied to {n} relations", fg=typer.colors.GREEN)


@app.command("create-tenant")
def create_tenant(name: str = typer.Option(..., "--name"), slug: str = typer.Option(..., "--slug"),
                  admin_email: str = typer.Option(..., "--admin-email"),
                  admin_password: Optional[str] = typer.Option(None, "--admin-password", help="Omit to be prompted"),
                  timezone: str = typer.Option("Asia/Kolkata", "--timezone")) -> None:
    """Create a tenant with the default ITIL configuration and its first administrator."""
    from .db.models import Tenant, User
    from .db.session import plain_session, tenant_session
    from .security.passwords import make_password
    from .services.bootstrap import install_defaults

    pwd = _password(admin_password, "Admin password")
    _check_password(pwd, email=admin_email)
    with plain_session() as ps:
        if ps.scalars(select(Tenant.id).where(Tenant.slug == slug)).first():
            _fail("A tenant with that slug already exists")
        tenant = Tenant(name=name, slug=slug, timezone=timezone, settings={})
        ps.add(tenant)
        ps.flush()
        tid = tenant.id
    with tenant_session(tid) as s:
        install_defaults(s, s.get(Tenant, tid))
        email = admin_email.lower()
        s.add(User(email=email, username=email, tenant_id=tid, role="manager", is_staff=True, first_name="Admin",
                   password=make_password(pwd)))
    typer.secho(f"Tenant '{name}' created (id={tid}); admin {admin_email.lower()}", fg=typer.colors.GREEN)


@app.command("create-user")
def create_user(tenant: str = typer.Option(..., "--tenant", help="Tenant slug"), email: str = typer.Option(..., "--email"),
                role: str = typer.Option("employee", "--role"), first_name: str = typer.Option("", "--first-name"),
                last_name: str = typer.Option("", "--last-name"), account: Optional[str] = typer.Option(None, "--account", help="Account code"),
                password: Optional[str] = typer.Option(None, "--password", help="Omit to be prompted")) -> None:
    """Create a user in a tenant. The web console does not create users: there is no invitation flow."""
    from .db.models import Account, Tenant, User
    from .db.models.core import PRIVILEGED_ROLES, ROLES
    from .db.session import plain_session, tenant_session
    from .security.passwords import make_password

    if role not in ROLES:
        _fail(f"Role must be one of: {', '.join(ROLES)}")
    pwd = _password(password)
    _check_password(pwd, email=email, first_name=first_name, last_name=last_name)
    with plain_session() as ps:
        tid = ps.scalar(select(Tenant.id).where(Tenant.slug == tenant))
        if tid is None:
            _fail("No such tenant")
        if ps.scalar(select(User.id).where(User.email == email.lower())):
            _fail("A user with that email already exists")
    try:
        with tenant_session(tid) as s:
            acct = None
            if account:
                acct = s.scalars(select(Account).where(Account.tenant_id == tid, Account.code == account.upper())).first()
                if acct is None:
                    _fail("No such account code in that tenant")
            s.add(User(email=email.lower(), username=email.lower(), tenant_id=tid, role=role, first_name=first_name,
                       last_name=last_name, is_staff=role in PRIVILEGED_ROLES, password=make_password(pwd),
                       account_id=acct.id if acct else None))
    except IntegrityError as exc:  # users are tenant-isolated, so the one-per-site rule is enforced by the database
        if "core_user_single_super_admin" in str(exc):
            _fail("A super admin already exists. There is exactly one for the whole site.")
        raise
    typer.secho(f"User {email.lower()} created as {role}", fg=typer.colors.GREEN)


@app.command("reset-password")
def reset_password(email: str = typer.Option(..., "--email"), password: Optional[str] = typer.Option(None, "--password")) -> None:
    """Set a new password for a user (and nothing else)."""
    from .db.models import User
    from .db.session import plain_session, tenant_session
    from .security.passwords import make_password

    with plain_session() as ps:  # users are tenant-isolated: find the tenant through the SECURITY DEFINER lookup
        found = ps.execute(text("SELECT user_id, tenant_id FROM public.auth_lookup_user_by_email(:e)"), {"e": email.strip().lower()}).first()
    if found is None or found.tenant_id is None:
        _fail("No such user")
    with tenant_session(found.tenant_id) as s:
        row = s.execute(select(User.id, User.first_name, User.last_name, User.username).where(User.id == found.user_id)).one()
    pwd = _password(password, "New password")
    _check_password(pwd, email=email, first_name=row.first_name, last_name=row.last_name, username=row.username)
    with tenant_session(found.tenant_id) as s:
        s.get(User, row.id).password = make_password(pwd)
    typer.secho("Password updated", fg=typer.colors.GREEN)


@app.command("seed-demo")
def seed_demo_cmd(reset: bool = typer.Option(False, "--reset", help="Recreate the demo tenant (needs owner credentials)")) -> None:
    """Load the demo tenant 'demo' (login manager@demo.opsdesk.dev)."""
    from .services.demo_seed import seed_demo

    seed_demo(reset=reset, log=typer.echo)


@app.command("reindex-embeddings")
def reindex_embeddings(tenant: Optional[str] = typer.Option(None, "--tenant", help="Tenant slug (default: all)")) -> None:
    """Re-embed all tickets and KB articles (run after changing AI_EMBEDDING_PROVIDER or model)."""
    from .ai.engine import embed_article, embed_ticket
    from .db.models import KnowledgeArticle, Tenant, Ticket
    from .db.session import plain_session, tenant_session

    with plain_session() as ps:
        stmt = select(Tenant.id, Tenant.slug)
        if tenant:
            stmt = stmt.where(Tenant.slug == tenant)
        tenants = ps.execute(stmt).all()
    for tid, slug in tenants:
        n = 0
        with tenant_session(tid) as s:
            for (ticket_id,) in s.execute(select(Ticket.id).where(Ticket.tenant_id == tid).order_by(Ticket.id)).all():
                embed_ticket(s, s.get(Ticket, ticket_id))
                n += 1
                if n % 200 == 0:
                    s.commit()
            for art in s.scalars(select(KnowledgeArticle).where(KnowledgeArticle.tenant_id == tid)):
                embed_article(s, art)
        typer.secho(f"{slug}: re-embedded {n} tickets", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- tenants
@tenants_app.command("list")
def tenants_list() -> None:
    from .db.models import Tenant
    from .db.session import plain_session

    with plain_session() as ps:
        for t in ps.scalars(select(Tenant).order_by(Tenant.id)):
            typer.echo(f"{t.id}\t{t.slug}\t{'active' if t.is_active else 'INACTIVE'}\t{t.timezone}\t{t.name}")


@tenants_app.command("set")
def tenants_set(slug: str = typer.Argument(...), name: Optional[str] = typer.Option(None), timezone: Optional[str] = typer.Option(None),
                active: Optional[bool] = typer.Option(None, "--active/--inactive")) -> None:
    """Rename a tenant, change its time zone, or deactivate/reactivate it."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from .db.models import Tenant
    from .db.session import plain_session

    if timezone:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            _fail("Unknown time zone")
    with plain_session() as ps:
        t = ps.scalars(select(Tenant).where(Tenant.slug == slug)).first()
        if t is None:
            _fail("No such tenant")
        if name:
            t.name = name
        if timezone:
            t.timezone = timezone
        if active is not None:
            t.is_active = active
    typer.secho("Updated", fg=typer.colors.GREEN)


@tenants_app.command("setting")
def tenants_setting(slug: str = typer.Argument(...), key: str = typer.Argument(...), value: Optional[str] = typer.Argument(None)) -> None:
    """Show or set a tenant setting (value is JSON): auto_close_days, customer_sees_account_tickets, csat_enabled,
    auto_apply_triage, triage_confidence_threshold."""
    from .db.models import Tenant
    from .db.session import plain_session

    with plain_session() as ps:
        t = ps.scalars(select(Tenant).where(Tenant.slug == slug)).first()
        if t is None:
            _fail("No such tenant")
        if value is None:
            typer.echo(json.dumps((t.settings or {}).get(key)))
            return
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            _fail("Value must be valid JSON (e.g. true, 5, 0.8, \"text\")")
        t.settings = {**(t.settings or {}), key: parsed}
    typer.secho("Updated", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- signing keys
@keys_app.command("list")
def keys_list() -> None:
    from .db.session import get_owner_engine

    with get_owner_engine().connect() as c:
        for r in c.execute(text("SELECT key_id, status, created_at, retired_at FROM opsdesk_secure.signing_keys ORDER BY created_at")):
            typer.echo(f"{r.key_id}\t{r.status}\t{r.created_at:%Y-%m-%d}\t{r.retired_at or ''}")


@keys_app.command("add")
def keys_add(key_id: str = typer.Argument(..., help="1-32 chars: letters, digits, - _")) -> None:
    """Generate a new signing key, store it, and print the env values to deploy (the secret is shown once)."""
    from .db import rls
    from .db.session import get_owner_engine

    secret = secrets.token_hex(32)
    try:
        sql = rls.seed_signing_key_sql(key_id, secret)
    except ValueError:
        _fail("Invalid key id")
    with get_owner_engine().begin() as c:
        if c.execute(text("SELECT 1 FROM opsdesk_secure.signing_keys WHERE key_id = :k"), {"k": key_id}).first():
            _fail("That key id already exists")
        c.exec_driver_sql(sql)
    typer.echo(f"OPSDESK_SIGNING_KEY_ID={key_id}\nOPSDESK_SIGNING_KEY_SECRET={secret}")
    typer.secho("Deploy these values to the API and workers, then retire the old key with `keys retire`.", fg=typer.colors.YELLOW)


@keys_app.command("retire")
def keys_retire(key_id: str = typer.Argument(...)) -> None:
    """Retire a key: contexts signed with it stop verifying. Retired keys never return to active."""
    from .db.session import get_owner_engine

    with get_owner_engine().begin() as c:
        n = c.execute(text("UPDATE opsdesk_secure.signing_keys SET status='retired', retired_at=now() "
                           "WHERE key_id=:k AND status='active'"), {"k": key_id}).rowcount
    if not n:
        _fail("No active key with that id")
    typer.secho("Retired", fg=typer.colors.GREEN)


@app.command("reencrypt-channels")
def reencrypt_channels() -> None:
    """Re-save every channel so its credentials are encrypted with the newest FIELD_ENCRYPTION_KEYS entry
    (run after prepending a new key, before removing the old one)."""
    from sqlalchemy.orm.attributes import flag_modified

    from .db.models import Channel, Tenant
    from .db.session import plain_session, tenant_session

    with plain_session() as ps:
        tenant_ids = list(ps.scalars(select(Tenant.id).order_by(Tenant.id)))
    total = 0
    for tid in tenant_ids:
        with tenant_session(tid) as s:
            for c in s.scalars(select(Channel)):
                flag_modified(c, "config")
                total += 1
    typer.echo(f"Re-encrypted {total} channel(s).")


@app.command()
def check() -> None:
    """Verify configuration and connectivity (database, Redis, signing key)."""
    from . import tenancy
    from .db.session import ping
    from .settings import settings

    ok = True
    try:
        kid, _ = settings.signing_config()
        typer.echo(f"signing key: {kid}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        typer.secho(f"signing key: {exc}", fg=typer.colors.RED)
    if ping():
        typer.echo("database: ok")
    else:
        ok = False
        typer.secho("database: unreachable", fg=typer.colors.RED)
    try:
        tenancy.get_redis().ping()
        typer.echo("redis: ok")
    except Exception as exc:  # noqa: BLE001
        ok = False
        typer.secho(f"redis: {type(exc).__name__}", fg=typer.colors.RED)
    raise typer.Exit(0 if ok else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
