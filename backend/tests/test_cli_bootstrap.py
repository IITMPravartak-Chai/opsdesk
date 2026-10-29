"""Operations CLI and the default-configuration installer."""
from sqlalchemy import func, select
from typer.testing import CliRunner

from opsdesk.cli import app
from opsdesk.db.models import (BusinessHours, Category, CustomField, Priority, PriorityMatrix, SLAPolicy, Tenant, TicketType, User,
                               Workflow)
from opsdesk.db.session import plain_session, tenant_session
from opsdesk.security import passwords
from opsdesk.services.bootstrap import install_defaults
from tests.conftest import make_world

run = CliRunner().invoke
PW = "Xk9!vQ2#mLw8pZ-cli"


def tid_of(slug):
    with plain_session() as s:
        return s.scalars(select(Tenant.id).where(Tenant.slug == slug)).one()


def user_row(slug, email):
    with tenant_session(tid_of(slug)) as s:
        return s.scalars(select(User).where(User.email == email)).one()


def counts(w):
    with tenant_session(w.tenant_id) as s:
        return {m.__name__: s.scalar(select(func.count()).select_from(m).where(m.tenant_id == w.tenant_id)) for m in
                (Workflow, TicketType, Priority, PriorityMatrix, SLAPolicy, Category, BusinessHours, CustomField)}


class TestDefaults:
    def test_installs_the_itil_starter_configuration(self, world):
        c = counts(world)
        assert c["Workflow"] == 4 and c["TicketType"] == 4 and c["Priority"] == 4 and c["PriorityMatrix"] == 16
        assert c["SLAPolicy"] == 2 and c["BusinessHours"] == 2 and c["CustomField"] == 11 and c["Category"] > 20
        with tenant_session(world.tenant_id) as s:
            incident = s.scalars(select(Workflow).where(Workflow.tenant_id == world.tenant_id, Workflow.name == "Incident")).one()
            assert [st.key for st in incident.states][:2] == ["new", "in_progress"]
            assert incident.customer_reply_state_id == next(st.id for st in incident.states if st.key == "in_progress")
            assert sum(1 for st in incident.states if st.is_initial) == 1

    def test_is_idempotent(self, world):
        before = counts(world)
        with tenant_session(world.tenant_id) as s:
            install_defaults(s, s.get(Tenant, world.tenant_id))
        assert counts(world) == before

    def test_tenant_settings_defaults_do_not_override_existing_values(self):
        w = make_world(default_config=False)
        with tenant_session(w.tenant_id) as s:
            t = s.get(Tenant, w.tenant_id)
            t.settings = {"auto_close_days": 9}
            install_defaults(s, t)
            assert t.settings["auto_close_days"] == 9 and t.settings["csat_enabled"] is True


class TestCli:
    def test_create_tenant_user_and_password_flow(self):
        r = run(app, ["create-tenant", "--name", "Cli Corp", "--slug", "cli-corp", "--admin-email", "root@cli.test", "--admin-password", PW])
        assert r.exit_code == 0, r.output
        assert run(app, ["create-tenant", "--name", "Dup", "--slug", "cli-corp", "--admin-email", "x@cli.test", "--admin-password", PW]).exit_code == 1
        assert run(app, ["create-user", "--tenant", "cli-corp", "--email", "ada@cli.test", "--role", "employee", "--password", PW]).exit_code == 0
        assert run(app, ["create-user", "--tenant", "cli-corp", "--email", "ada@cli.test", "--password", PW]).exit_code == 1  # exists
        assert run(app, ["create-user", "--tenant", "nope", "--email", "z@cli.test", "--password", PW]).exit_code == 1
        assert run(app, ["create-user", "--tenant", "cli-corp", "--email", "w@cli.test", "--password", "short"]).exit_code == 1
        assert run(app, ["create-user", "--tenant", "cli-corp", "--email", "w@cli.test", "--role", "wizard", "--password", PW]).exit_code == 1
        ada, admin = user_row("cli-corp", "ada@cli.test"), user_row("cli-corp", "root@cli.test")
        if True:
            assert ada.role == "employee" and admin.role == "manager" and admin.is_staff and passwords.check_password(PW, ada.password)
        assert run(app, ["reset-password", "--email", "ada@cli.test", "--password", "Another!Passw0rd-2"]).exit_code == 0
        assert run(app, ["reset-password", "--email", "ghost@cli.test", "--password", PW]).exit_code == 1
        assert passwords.check_password("Another!Passw0rd-2", user_row("cli-corp", "ada@cli.test").password)

    def test_cli_created_admin_can_sign_in_over_http(self, api):
        run(app, ["create-tenant", "--name", "Http Corp", "--slug", "http-corp", "--admin-email", "root@http.test", "--admin-password", PW])
        r = api.post("/api/auth/login/", json={"email": "root@http.test", "password": PW})
        assert r.status_code == 200 and r.json()["user"]["role"] == "manager" and r.json()["user"]["tenant"] == "Http Corp"

    def test_tenant_commands(self):
        run(app, ["create-tenant", "--name", "T One", "--slug", "t-one", "--admin-email", "a@t1.test", "--admin-password", PW])
        assert "t-one" in run(app, ["tenants", "list"]).output
        assert run(app, ["tenants", "set", "t-one", "--name", "Renamed", "--timezone", "Europe/Paris", "--inactive"]).exit_code == 0
        assert run(app, ["tenants", "set", "t-one", "--timezone", "Mars/Base"]).exit_code == 1
        assert run(app, ["tenants", "set", "nope", "--name", "x"]).exit_code == 1
        listing = run(app, ["tenants", "list"]).output
        assert "INACTIVE" in listing and "Renamed" in listing
        assert run(app, ["tenants", "setting", "t-one", "auto_close_days", "11"]).exit_code == 0
        assert run(app, ["tenants", "setting", "t-one", "auto_close_days"]).output.strip() == "11"
        assert run(app, ["tenants", "setting", "t-one", "x", "{bad"]).exit_code == 1

    def test_inactive_tenant_users_cannot_sign_in(self, api):
        run(app, ["create-tenant", "--name", "Gone", "--slug", "gone-co", "--admin-email", "a@gone.test", "--admin-password", PW])
        run(app, ["tenants", "set", "gone-co", "--inactive"])
        assert api.post("/api/auth/login/", json={"email": "a@gone.test", "password": PW}).status_code == 401

    def test_check_reports_ok(self):
        r = run(app, ["check"])
        assert r.exit_code == 0 and "database: ok" in r.output and "signing key: k1" in r.output

    def test_signing_keys_lifecycle(self):
        assert "k1\tactive" in run(app, ["keys", "list"]).output
        r = run(app, ["keys", "add", "k-test-2"])
        assert r.exit_code == 0 and "OPSDESK_SIGNING_KEY_ID=k-test-2" in r.output
        secret = [ln for ln in r.output.splitlines() if ln.startswith("OPSDESK_SIGNING_KEY_SECRET=")][0].split("=", 1)[1]
        assert len(secret) == 64
        assert run(app, ["keys", "add", "k-test-2"]).exit_code == 1
        assert run(app, ["keys", "add", "bad key!"]).exit_code == 1
        assert run(app, ["keys", "retire", "k-test-2"]).exit_code == 0
        assert run(app, ["keys", "retire", "k-test-2"]).exit_code == 1  # no longer active

    def test_seed_demo_and_reset(self):
        first = run(app, ["seed-demo", "--reset"])
        assert first.exit_code == 0 and "Demo ready" in first.output
        assert "exists" in run(app, ["seed-demo"]).output
        with plain_session() as s:
            tid = s.scalars(select(Tenant).where(Tenant.slug == "demo")).one().id
        with tenant_session(tid) as s:
            from opsdesk.db.models import Channel, KnowledgeArticle, Rule, Ticket
            assert s.scalar(select(func.count()).select_from(Ticket)) == 26
            assert s.scalar(select(func.count()).select_from(Channel)) == 3 and s.scalar(select(func.count()).select_from(Rule)) == 4
            assert s.scalar(select(func.count()).select_from(KnowledgeArticle)) == 6
            assert s.scalar(select(func.count()).select_from(Ticket).where(Ticket.embedding.is_not(None))) == 26
        again = run(app, ["seed-demo", "--reset"])
        assert again.exit_code == 0
        with plain_session() as s:
            assert s.scalar(select(Tenant.id).where(Tenant.slug == "demo")) != tid  # recreated

    def test_reindex_embeddings(self):
        run(app, ["create-tenant", "--name", "Idx", "--slug", "idx-co", "--admin-email", "a@idx.test", "--admin-password", PW])
        r = run(app, ["reindex-embeddings", "--tenant", "idx-co"])
        assert r.exit_code == 0 and "idx-co: re-embedded 0 tickets" in r.output

    def test_reencrypt_channels_keeps_configs_readable(self, world):
        from opsdesk.db.models import Channel
        with tenant_session(world.tenant_id) as s:
            s.add(Channel(tenant_id=world.tenant_id, kind="webhook", name="Hook", inbound_token="t" * 32,
                          config={"secret": "s3cret"}, settings={}))
        with tenant_session(world.tenant_id) as s:
            before = {c.id: dict(c.config) for c in s.scalars(select(Channel))}
        assert before
        r = run(app, ["reencrypt-channels"])
        assert r.exit_code == 0 and "Re-encrypted" in r.output
        with tenant_session(world.tenant_id) as s:
            assert {c.id: dict(c.config) for c in s.scalars(select(Channel))} == before
