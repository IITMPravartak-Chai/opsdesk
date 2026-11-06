"""Tenant isolation: signed context, replay protection and PostgreSQL row-level security, against a real database."""
import time

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from opsdesk import tenancy
from opsdesk.db.models import Account, AuditLog, Team, TeamMember, Ticket, Workflow, State, User
from opsdesk.db.session import plain_session, tenant_session
from opsdesk.errors import InvalidTenantContextError, ReplayDetectedError, SecurityError
from tests.conftest import make_world


class TestSignedClaims:
    def test_mint_validate_roundtrip(self):
        c = tenancy.sign_tenant_context(42)
        tid, ts, kid, nonce, sig = tenancy.validate_signed_claims(c)
        assert tid == 42 and kid == "k1" and len(nonce) == 32 and len(sig) == 64

    def test_each_context_can_be_claimed_exactly_once(self):
        c = tenancy.sign_tenant_context(1)
        assert tenancy.claim_signed_context(c) is True
        with pytest.raises(ReplayDetectedError):
            tenancy.claim_signed_context(c)

    @pytest.mark.parametrize("field,value", [
        ("tenant_id", "2"), ("signature", "0" * 64), ("nonce", "f" * 32), ("key_id", "k2"), ("timestamp", "1700000000"),
    ])
    def test_tampering_with_any_field_is_rejected(self, field, value):
        c = tenancy.sign_tenant_context(1)
        c[field] = value
        with pytest.raises(InvalidTenantContextError):
            tenancy.validate_signed_claims(c)

    @pytest.mark.parametrize("bad", [None, [], "x", {}, {"tenant_id": 1}])
    def test_malformed_claims_rejected(self, bad):
        with pytest.raises(InvalidTenantContextError):
            tenancy.validate_signed_claims(bad)

    def test_expired_and_future_contexts_rejected(self, monkeypatch):
        c = tenancy.sign_tenant_context(1)
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + 61)
        with pytest.raises(InvalidTenantContextError):
            tenancy.validate_signed_claims(c)
        monkeypatch.setattr(time, "time", lambda: real() - 10)
        with pytest.raises(InvalidTenantContextError):
            tenancy.validate_signed_claims(c)

    def test_rls_bypass_helper_is_discontinued(self):
        with pytest.raises(SecurityError):
            tenancy.system_context()

    def test_session_requires_a_positive_tenant(self):
        with pytest.raises(Exception):
            with tenant_session(0):
                pass


class TestDatabaseVerifier:
    """public.get_verified_tenant_id(): the database re-checks signature, key, format and freshness itself."""

    def _verified(self, conn, claims):
        conn.execute(text("SELECT set_config('app.tenant_id', :a, true), set_config('app.tenant_timestamp', :b, true), "
                          "set_config('app.tenant_key_id', :c, true), set_config('app.tenant_nonce', :d, true), "
                          "set_config('app.tenant_signature', :e, true)"),
                     dict(zip("abcde", (claims[k] for k in tenancy.CLAIM_FIELDS))))
        return conn.execute(text("SELECT public.get_verified_tenant_id()")).scalar()

    def test_valid_context_is_verified(self, world):
        from opsdesk.db.session import get_engine
        with get_engine().connect() as conn:
            assert self._verified(conn, tenancy.sign_tenant_context(world.tenant_id)) == world.tenant_id

    @pytest.mark.parametrize("mutate", [
        lambda c: {**c, "signature": "0" * 64},
        lambda c: {**c, "tenant_id": str(int(c["tenant_id"]) + 1)},
        lambda c: {**c, "key_id": "nope"},
        lambda c: {**c, "timestamp": str(int(c["timestamp"]) - 120)},
        lambda c: {**c, "nonce": "z" * 32},
    ])
    def test_invalid_context_yields_no_tenant(self, world, mutate):
        from opsdesk.db.session import get_engine
        with get_engine().connect() as conn:
            assert self._verified(conn, mutate(tenancy.sign_tenant_context(world.tenant_id))) is None


class TestRowLevelSecurity:
    def test_a_tenant_sees_only_its_own_rows(self, world, other_world):
        with tenant_session(world.tenant_id) as s:
            mine = {a.tenant_id for a in s.scalars(select(Account))}
            teams = list(s.scalars(select(Team)))
        assert mine == {world.tenant_id}
        with tenant_session(other_world.tenant_id) as s:
            assert {a.tenant_id for a in s.scalars(select(Account))} == {other_world.tenant_id}

    def test_no_context_sees_nothing(self, world):
        with plain_session() as s:
            assert s.scalars(select(Account)).all() == []
            assert s.scalars(select(Ticket)).all() == []

    def test_cross_tenant_insert_is_blocked_by_with_check(self, world, other_world):
        with pytest.raises(DBAPIError):
            with tenant_session(world.tenant_id) as s:
                s.add(Account(tenant_id=other_world.tenant_id, name="Evil", code="EVL", notes=""))
                s.flush()

    def test_cross_tenant_update_and_delete_touch_nothing(self, world, other_world):
        with tenant_session(other_world.tenant_id) as s:
            victim = s.scalars(select(Account)).first().id
        with tenant_session(world.tenant_id) as s:
            assert s.execute(update(Account).where(Account.id == victim).values(name="pwned")).rowcount == 0
            assert s.execute(text("DELETE FROM core_account WHERE id = :i"), {"i": victim}).rowcount == 0
        with tenant_session(other_world.tenant_id) as s:
            assert s.get(Account, victim).name != "pwned"

    def test_child_tables_without_tenant_column_are_isolated_through_their_parent(self, world, other_world):
        with tenant_session(world.tenant_id) as s:
            own = s.scalars(select(Workflow.id)).all()
            states = s.scalars(select(State)).all()
            assert states and {st.workflow_id for st in states} <= set(own)

    def test_many_to_many_tables_are_isolated(self, world, other_world):
        with tenant_session(other_world.tenant_id) as s:
            t = Team(tenant_id=other_world.tenant_id, name="Ops", email="", assignment_strategy="manual", rr_cursor=0)
            s.add(t)
            s.flush()
            s.add(TeamMember(team_id=t.id, user_id=other_world.users["employee"]))
        with tenant_session(world.tenant_id) as s:
            assert s.scalar(select(text("count(*)")).select_from(TeamMember)) == 0

    def test_context_does_not_leak_to_the_next_user_of_a_pooled_connection(self, world):
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(Account)).all()
        for _ in range(5):
            with plain_session() as s:
                assert s.scalars(select(Account)).all() == []

    def test_context_survives_a_commit_inside_the_session(self, world):
        with tenant_session(world.tenant_id) as s:
            s.commit()
            assert s.scalars(select(Account)).all()

    def test_audit_log_is_append_only(self, world):
        from opsdesk.services.audit import audit
        with tenant_session(world.tenant_id) as s:
            row = audit(s, world.tenant_id, "test.event", None, None, {"a": 1})
            s.flush()
            row_id = row.id
        with pytest.raises(DBAPIError, match="append-only"):
            with tenant_session(world.tenant_id) as s:
                s.execute(text("UPDATE core_auditlog SET action = 'x' WHERE id = :i"), {"i": row_id})
        with pytest.raises(DBAPIError, match="append-only"):
            with tenant_session(world.tenant_id) as s:
                s.execute(text("DELETE FROM core_auditlog WHERE id = :i"), {"i": row_id})

    def test_login_lookup_function_works_without_a_tenant_and_never_leaks_other_columns(self, world):
        with plain_session() as s:
            row = s.execute(text("SELECT * FROM public.auth_lookup_user_by_email(:e)"), {"e": world.email("employee").upper()}).first()
        assert row.user_id == world.users["employee"] and row.tenant_id == world.tenant_id
        assert set(row._fields) == {"user_id", "email", "password_hash", "is_active", "tenant_id", "tenant_is_active",
                                    "mfa_enabled", "role"}

    def test_login_lookup_refuses_ambiguous_or_blank_input(self):
        with plain_session() as s:
            assert s.execute(text("SELECT * FROM public.auth_lookup_user_by_email(:e)"), {"e": "  "}).first() is None
            assert s.execute(text("SELECT * FROM public.auth_lookup_user_by_email(:e)"), {"e": "nobody@nowhere.example"}).first() is None

    def test_app_role_cannot_read_the_signing_keystore(self):
        with pytest.raises(DBAPIError):
            with plain_session() as s:
                s.execute(text("SELECT * FROM opsdesk_secure.signing_keys")).all()

    def test_service_layer_refuses_a_session_bound_to_another_tenant(self, world, other_world):
        from opsdesk.services import tickets as svc
        with tenant_session(world.tenant_id) as s:
            with pytest.raises(SecurityError):
                svc.assert_session_tenant(s, other_world.tenant_id)
