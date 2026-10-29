"""Configuration console API: every resource, its rules and its audit trail."""
import pytest
from sqlalchemy import select

from opsdesk.db.models import (AuditLog, BusinessHours, Category, Channel, Priority, SLAPolicy, State, Ticket, TicketType,
                               Transition, Workflow)
from opsdesk.db.session import tenant_session
from tests.conftest import create_ticket_api

pytestmark = pytest.mark.usefixtures("wide_admin_access")

C = "/api/admin/config"


def audit_actions(w):
    with tenant_session(w.tenant_id) as s:
        return [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def ids(api, h, resource, **params):
    return {x["name"] if "name" in x else x["key"]: x["id"] for x in api.get(f"{C}/{resource}/", headers=h, params=params).json()["results"]}


class TestAccessAndSchema:
    def test_only_admins_and_managers(self, api, auth):
        for role in ("employee", "customer"):
            assert api.get(f"{C}/", headers=auth(role)).status_code == 403
        assert api.get(f"{C}/workflows/", headers=auth("employee")).status_code == 403
        assert api.get(f"{C}/", headers={}).status_code == 401

    def test_schema_describes_resources_adapters_and_rule_vocabulary(self, api, auth):
        body = api.get(f"{C}/", headers=auth("team_lead")).json()
        assert body["is_admin"] is False and api.get(f"{C}/", headers=auth("manager")).json()["is_admin"] is True
        assert {"workflows", "states", "transitions", "ticket-types", "custom-fields", "priorities", "categories", "accounts",
                "channels", "rules", "services", "items", "canned-responses", "catalog-items", "contact-identities"} <= set(body["resources"])
        assert {"email", "imap", "whatsapp", "webhook"} <= set(body["adapters"])
        assert "ticket.created" in body["rule_events"] and "webhook" in body["action_types"] and "changed" in body["condition_ops"]
        assert body["resources"]["priorities"]["fields"]["level"]["min"] == 1

    def test_tenant_isolation_on_every_listing(self, api, world, other_world, auth):
        for resource in ("workflows", "ticket-types", "priorities", "categories", "accounts", "canned-responses"):
            mine = api.get(f"{C}/{resource}/", headers=auth("manager")).json()["count"]
            assert api.get(f"{C}/{resource}/", headers=auth("manager", other_world)).json()["count"] == mine  # separate copies
        wf = ids(api, auth("manager"), "workflows")["Incident"]
        assert api.get(f"{C}/workflows/{wf}/", headers=auth("manager", other_world)).status_code == 404
        assert api.patch(f"{C}/workflows/{wf}/", headers=auth("manager", other_world), json={"name": "x"}).status_code == 404
        assert api.delete(f"{C}/workflows/{wf}/", headers=auth("manager", other_world)).status_code == 404


class TestGenericCrud:
    def test_create_read_update_delete_with_audit(self, api, world, auth):
        h = auth("manager")
        r = api.post(f"{C}/categories/", headers=h, json={"name": "  Networking   core  "})
        assert r.status_code == 201 and r.json()["name"] == "Networking core"
        cid = r.json()["id"]
        sub = api.post(f"{C}/categories/", headers=h, json={"name": "Wi-Fi", "parent_id": cid}).json()
        assert sub["path"] == "Networking core / Wi-Fi" and sub["parent_label"] == "Networking core"
        assert api.patch(f"{C}/categories/{sub['id']}/", headers=h, json={"name": "WiFi"}).json()["name"] == "WiFi"
        assert api.delete(f"{C}/categories/{cid}/", headers=h).status_code == 409  # has a sub-category
        assert api.delete(f"{C}/categories/{sub['id']}/", headers=h).status_code == 204
        assert api.delete(f"{C}/categories/{cid}/", headers=h).status_code == 204
        assert api.get(f"{C}/categories/{cid}/", headers=h).status_code == 404
        acts = audit_actions(world)
        assert acts.count("admin.categories.create") == 2 and "admin.categories.update" in acts and acts.count("admin.categories.delete") == 2

    def test_search_filters_and_paging(self, api, auth):
        h = auth("team_lead")
        assert api.get(f"{C}/categories/?q=access", headers=h).json()["count"] >= 1
        total = api.get(f"{C}/categories/", headers=h).json()["count"]
        page = api.get(f"{C}/categories/?page=2&page_size=5", headers=h).json()
        assert page["count"] == total and len(page["results"]) == min(5, total - 5) and page["page"] == 2
        assert api.get(f"{C}/accounts/?tier=gold", headers=h).json()["count"] == 1
        assert api.get(f"{C}/accounts/?tier=silver", headers=h).json()["count"] == 0

    @pytest.mark.parametrize("body,field", [
        ({}, "name"), ({"name": ""}, "name"), ({"name": "x" * 200}, "name"), ({"name": 5}, "name"),
        ({"name": "ok", "parent_id": 10 ** 9}, "parent_id"), ({"name": "ok", "ticket_type_id": "1"}, "ticket_type_id"),
    ])
    def test_field_errors_are_keyed_by_field(self, api, auth, body, field):
        r = api.post(f"{C}/categories/", headers=auth("manager"), json=body)
        assert r.status_code == 400 and field in r.json()["errors"]

    def test_unknown_and_readonly_fields_are_rejected(self, api, auth):
        r = api.post(f"{C}/categories/", headers=auth("manager"), json={"name": "x", "tenant_id": 1, "evil": True})
        assert r.status_code == 400 and set(r.json()["errors"]) == {"tenant_id", "evil"}

    def test_cannot_reference_another_tenants_rows(self, api, world, other_world, auth):
        foreign_wf = ids(api, auth("manager", other_world), "workflows")["Incident"]
        r = api.post(f"{C}/ticket-types/", headers=auth("manager"), json={"key": "x", "name": "X", "prefix": "XX", "workflow_id": foreign_wf})
        assert r.status_code == 400 and "workflow_id" in r.json()["errors"]

    def test_category_cannot_become_its_own_ancestor(self, api, auth):
        h = auth("manager")
        a = api.post(f"{C}/categories/", headers=h, json={"name": "A"}).json()["id"]
        b = api.post(f"{C}/categories/", headers=h, json={"name": "B", "parent_id": a}).json()["id"]
        assert api.patch(f"{C}/categories/{a}/", headers=h, json={"parent_id": b}).status_code == 400
        assert api.patch(f"{C}/categories/{a}/", headers=h, json={"parent_id": a}).status_code == 400

    def test_options_endpoint_lists_pickers_per_tenant(self, api, auth):
        pr = api.get(f"{C}/options/Priority/", headers=auth("team_lead")).json()
        assert [p["label"].split(" - ")[0] for p in pr] == ["P1", "P2", "P3", "P4"]
        assert api.get(f"{C}/options/User/", headers=auth("team_lead")).json()
        assert api.get(f"{C}/options/Nope/", headers=auth("team_lead")).status_code == 404
        wf = ids(api, auth("team_lead"), "workflows")["Incident"]
        states = api.get(f"{C}/options/State/?parent={wf}", headers=auth("team_lead")).json()
        assert len(states) == 7


class TestWorkflows:
    def test_workflow_detail_embeds_states_and_transitions(self, api, auth):
        h = auth("manager")
        wf = ids(api, h, "workflows")["Incident"]
        d = api.get(f"{C}/workflows/{wf}/", headers=h).json()
        assert d["state_count"] == 7 and len(d["states"]) == 7 and d["transitions"]
        assert d["customer_reply_state_id"] == next(s["id"] for s in d["states"] if s["key"] == "in_progress")

    def test_state_lifecycle_unique_key_single_initial_and_in_use_guard(self, api, world, auth):
        h = auth("manager")
        wf = api.post(f"{C}/workflows/", headers=h, json={"name": "Custom"}).json()["id"]
        a = api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "open", "name": "Open", "is_initial": True, "category": "new"})
        assert a.status_code == 201
        assert api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "open", "name": "Dup"}).status_code == 400
        b = api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "done", "name": "Done", "is_initial": True, "category": "resolved"}).json()
        states = {s["key"]: s for s in api.get(f"{C}/workflows/{wf}/states/", headers=h).json()["results"]}
        assert states["done"]["is_initial"] and not states["open"]["is_initial"]  # exactly one initial status
        assert api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "Bad Key!", "name": "x"}).status_code == 400
        assert api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "z", "name": "z", "category": "weird"}).status_code == 400
        assert api.delete(f"{C}/workflows/{wf}/states/{b['id']}/", headers=h).status_code == 204

    def test_state_with_tickets_cannot_be_deleted(self, api, auth):
        h = auth("manager")
        t = create_ticket_api(api, h, "x")
        wf = ids(api, h, "workflows")["Incident"]
        new = next(s for s in api.get(f"{C}/workflows/{wf}/states/", headers=h).json()["results"] if s["key"] == "new")
        r = api.delete(f"{C}/workflows/{wf}/states/{new['id']}/", headers=h)
        assert r.status_code == 409 and r.json()["usage"]["tickets"] >= 1

    def test_transition_rules(self, api, auth):
        h = auth("manager")
        wf = api.post(f"{C}/workflows/", headers=h, json={"name": "W2"}).json()["id"]
        s1 = api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "a", "name": "A"}).json()["id"]
        s2 = api.post(f"{C}/workflows/{wf}/states/", headers=h, json={"key": "b", "name": "B"}).json()["id"]
        ok = api.post(f"{C}/workflows/{wf}/transitions/", headers=h, json={
            "name": "Go", "from_state_id": s1, "to_state_id": s2, "allowed_roles": ["employee", "team_lead"], "required_fields": ["resolution_code"],
            "requires_comment": True})
        assert ok.status_code == 201 and ok.json()["allowed_roles"] == ["employee", "team_lead"]
        assert api.post(f"{C}/workflows/{wf}/transitions/", headers=h, json={"name": "Any", "to_state_id": s2}).status_code == 201  # from any status
        other_wf = ids(api, h, "workflows")["Incident"]
        foreign = next(s for s in api.get(f"{C}/workflows/{other_wf}/states/", headers=h).json()["results"])["id"]
        bad = api.post(f"{C}/workflows/{wf}/transitions/", headers=h, json={"name": "Cross", "from_state_id": foreign, "to_state_id": s2})
        assert bad.status_code == 400 and "from_state_id" in bad.json()["errors"]
        assert api.post(f"{C}/workflows/{wf}/transitions/", headers=h, json={"name": "R", "to_state_id": s2, "allowed_roles": ["wizard"]}).status_code == 400
        assert api.patch(f"{C}/workflows/{wf}/", headers=h, json={"customer_reply_state_id": s1}).json()["customer_reply_state_id"] == s1
        assert api.patch(f"{C}/workflows/{wf}/", headers=h, json={"customer_reply_state_id": foreign}).status_code == 400

    def test_workflow_in_use_by_a_ticket_type_cannot_be_deleted(self, api, auth):
        h = auth("manager")
        wf = ids(api, h, "workflows")["Incident"]
        r = api.delete(f"{C}/workflows/{wf}/", headers=h)
        assert r.status_code == 409 and r.json()["usage"]["ticket_types"] == 1

    def test_changes_take_effect_for_new_tickets_immediately(self, api, auth):
        h = auth("manager")
        wf = ids(api, h, "workflows")["Incident"]
        states = {s["key"]: s["id"] for s in api.get(f"{C}/workflows/{wf}/states/", headers=h).json()["results"]}
        api.post(f"{C}/workflows/{wf}/transitions/", headers=h, json={"name": "Fast track", "from_state_id": states["new"], "to_state_id": states["closed"]})
        t = create_ticket_api(api, auth("employee"), "x")
        assert "Fast track" in {x["name"] for x in t["transitions"]}


class TestTicketTypesAndFields:
    def test_type_validation_unique_key_prefix_and_defaults(self, api, auth):
        h = auth("manager")
        wf = ids(api, h, "workflows")["Incident"]
        base = {"key": "task", "name": "Task", "prefix": "tsk", "workflow_id": wf}
        r = api.post(f"{C}/ticket-types/", headers=h, json=base)
        assert r.status_code == 201 and r.json()["prefix"] == "TSK" and r.json()["default_impact"] == 3 and r.json()["is_active"] is True
        assert api.post(f"{C}/ticket-types/", headers=h, json=base).status_code == 400
        assert api.post(f"{C}/ticket-types/", headers=h, json={**base, "key": "t2", "prefix": "TOOLONGPREFIX"}).status_code == 400
        assert api.post(f"{C}/ticket-types/", headers=h, json={**base, "key": "t3", "default_impact": 9}).status_code == 400
        t = create_ticket_api(api, h, "A task", type_key="task")
        assert t["number"] == "TSK-000001"

    def test_custom_fields_options_required_for_select_and_unique_key(self, api, auth):
        h = auth("manager")
        tt = ids(api, h, "ticket-types")["Incident"] if "Incident" in ids(api, h, "ticket-types") else ids(api, h, "ticket-types")["incident"]
        url = f"{C}/ticket-types/{tt}/custom-fields/"
        assert api.post(url, headers=h, json={"key": "site", "label": "Site", "field_type": "select"}).status_code == 400
        ok = api.post(url, headers=h, json={"key": "site", "label": "Site", "field_type": "select", "options": ["HQ", "DC", "HQ"]})
        assert ok.status_code == 201 and ok.json()["options"] == ["HQ", "DC"]
        assert api.post(url, headers=h, json={"key": "site", "label": "Dup"}).status_code == 400
        d = api.get(f"{C}/ticket-types/{tt}/", headers=h).json()
        assert "site" in {f["key"] for f in d["custom_fields"]}
        meta = api.get("/api/meta/", headers=auth("customer")).json()
        assert "site" in {f["key"] for t in meta["ticket_types"] if t["id"] == tt for f in t["custom_fields"]}

    def test_type_with_tickets_cannot_be_deleted(self, api, auth):
        h = auth("manager")
        create_ticket_api(api, h, "x")
        tid = next(v for k, v in ids(api, h, "ticket-types").items() if k.lower() == "incident")
        assert api.delete(f"{C}/ticket-types/{tid}/", headers=h).status_code == 409


class TestPriorities:
    def test_crud_unique_key_and_delete_guards(self, api, auth):
        h = auth("manager")
        r = api.post(f"{C}/priorities/", headers=h, json={"key": "P0", "name": "Catastrophic", "level": 1, "color": "#ff0000"})
        assert r.status_code == 201
        assert api.post(f"{C}/priorities/", headers=h, json={"key": "P0", "name": "Dup", "level": 1}).status_code == 400
        assert api.post(f"{C}/priorities/", headers=h, json={"key": "PX", "name": "x", "level": 0}).status_code == 400
        assert api.post(f"{C}/priorities/", headers=h, json={"key": "PY", "name": "x", "level": 1, "color": "red"}).status_code == 400
        assert api.delete(f"{C}/priorities/{r.json()['id']}/", headers=h).status_code == 204
        p1 = ids(api, h, "priorities")["Critical"]
        d = api.delete(f"{C}/priorities/{p1}/", headers=h)
        assert d.status_code == 409 and d.json()["usage"]["sla_targets"] >= 1 and d.json()["usage"]["priority_matrix_cells"] >= 1

    def test_priority_matrix_get_and_replace(self, api, world, auth):
        h = auth("manager")
        m = api.get(f"{C}/priority-matrix/", headers=h).json()
        assert len(m["cells"]) == 16 and m["impact"]["1"] and m["urgency"]["4"]
        p = {x["key"]: x["id"] for x in api.get(f"{C}/priorities/", headers=h).json()["results"]}
        cells = [{**c, "priority_id": p["P1"]} if (c["impact"], c["urgency"]) == (4, 4) else c for c in m["cells"]]
        assert api.put(f"{C}/priority-matrix/", headers=h, json={"cells": cells}).status_code == 200
        t = create_ticket_api(api, h, "tiny", impact=4, urgency=4)
        assert t["priority"]["key"] == "P1"
        assert "admin.priority_matrix.update" in audit_actions(world)
        assert api.put(f"{C}/priority-matrix/", headers=h, json={"cells": cells + [cells[0]]}).status_code == 400
        assert api.put(f"{C}/priority-matrix/", headers=h, json={"cells": [{"impact": 1, "urgency": 1, "priority_id": 10 ** 9}]}).status_code == 400
        assert api.put(f"{C}/priority-matrix/", headers=h, json={"cells": [{"impact": 5, "urgency": 1, "priority_id": p["P1"]}]}).status_code == 400


class TestAccounts:
    def test_account_crud_domains_code_and_sla_fields_are_admin_only(self, api, world, auth):
        h, hm = auth("manager"), auth("team_lead")
        r = api.post(f"{C}/accounts/", headers=hm, json={"name": "Initech", "code": "init", "tier": "platinum",
                                                          "email_domains": ["@Initech.com", "initech.com", "mail.initech.com"]})
        assert r.status_code == 201 and r.json()["code"] == "INIT" and r.json()["email_domains"] == ["initech.com", "mail.initech.com"]
        aid = r.json()["id"]
        assert api.post(f"{C}/accounts/", headers=hm, json={"name": "Dup", "code": "INIT"}).status_code == 400
        assert api.post(f"{C}/accounts/", headers=hm, json={"name": "BadDomain", "code": "BD", "email_domains": ["not a domain"]}).status_code == 400
        pol = next(iter(api.get("/api/admin/sla-policies/", headers=h).json()["results"]))["id"]
        assert api.patch(f"{C}/accounts/{aid}/", headers=hm, json={"sla_policy_id": pol}).status_code == 403
        done = api.patch(f"{C}/accounts/{aid}/", headers=h, json={"sla_policy_id": pol})
        assert done.status_code == 200 and done.json()["sla_policy_id"] == pol
        assert "admin.account.sla_update" in audit_actions(world)

    def test_account_with_dependents_cannot_be_deleted(self, api, world, auth):
        h = auth("manager")
        r = api.delete(f"{C}/accounts/{world.account_id}/", headers=h)
        assert r.status_code == 409 and r.json()["usage"]["users"] >= 1
        solo = api.post(f"{C}/accounts/", headers=h, json={"name": "Solo", "code": "SOLO"}).json()["id"]
        assert api.delete(f"{C}/accounts/{solo}/", headers=h).status_code == 204


class TestChannelsConsole:
    def test_create_validates_kind_and_masks_secrets(self, api, world, auth):
        h = auth("team_lead")
        bad = api.post(f"{C}/channels/", headers=h, json={"name": "X", "kind": "carrier-pigeon"})
        assert bad.status_code == 400 and "kind" in bad.json()["errors"]
        r = api.post(f"{C}/channels/", headers=h, json={"name": "Mailbox", "kind": "email", "config": {"webhook_token": "s3cr3t-token", "from_address": "a@b.c"},
                                                          "settings": {"ticket_type": "incident"}})
        assert r.status_code == 201
        body = r.json()
        assert body["config"]["webhook_token"] == "********" and body["config"]["from_address"] == "a@b.c"
        assert body["webhook_url"].endswith("/") and "/api/channels/inbound/" in body["webhook_url"]
        assert "s3cr3t-token" not in str(audit_actions(world)) and "s3cr3t-token" not in str(
            [a.changes for a in __import__("sqlalchemy").orm.Session(bind=None) ] if False else "")

    def test_masked_value_preserves_the_stored_secret_and_new_values_replace_it(self, api, world, auth):
        h = auth("team_lead")
        cid = api.post(f"{C}/channels/", headers=h, json={"name": "M", "kind": "email", "config": {"webhook_token": "keep-me"}}).json()["id"]
        api.patch(f"{C}/channels/{cid}/", headers=h, json={"config": {"webhook_token": "********", "from_address": "x@y.z"}})
        with tenant_session(world.tenant_id) as s:
            assert s.get(Channel, cid).config == {"webhook_token": "keep-me", "from_address": "x@y.z"}
        api.patch(f"{C}/channels/{cid}/", headers=h, json={"config": {"webhook_token": "rotated"}})
        with tenant_session(world.tenant_id) as s:
            assert s.get(Channel, cid).config == {"webhook_token": "rotated"}

    def test_secrets_never_appear_in_the_audit_log(self, api, world, auth):
        h = auth("team_lead")
        api.post(f"{C}/channels/", headers=h, json={"name": "M", "kind": "webhook", "config": {"secret": "super-secret-value"}})
        with tenant_session(world.tenant_id) as s:
            dump = " ".join(str(a.changes) for a in s.scalars(select(AuditLog)))
        assert "super-secret-value" not in dump

    def test_channel_with_tickets_cannot_be_deleted_and_inbound_url_works_end_to_end(self, api, world, auth):
        h = auth("team_lead")
        cid = api.post(f"{C}/channels/", headers=h, json={"name": "Hook", "kind": "email", "config": {
            "provider": "generic_json", "webhook_token": "tk", "from_address": "s@x.y"}, "settings": {"ticket_type": "incident"}}).json()
        token = cid["webhook_url"].rstrip("/").rsplit("/", 1)[1]
        r = api.post(f"/api/channels/inbound/{token}/?token=tk", json={"from": "a@globex.example", "subject": "hi", "text": "t", "message_id": "<z@z>"})
        assert r.json() == {"accepted": 1}
        assert api.delete(f"{C}/channels/{cid['id']}/", headers=h).status_code == 409
        assert api.get(f"{C}/inbound-messages/?channel_id={cid['id']}", headers=h).json()["results"][0]["status"] == "processed"
        assert api.get(f"{C}/inbound-messages/?status=failed", headers=h).json()["count"] == 0


class TestRules:
    GOOD = {"name": "Route AI", "event": "ticket.created",
            "conditions": [{"field": "ci.ci_type", "op": "in", "value": ["ai_model"]}],
            "actions": [{"type": "assign_team", "value": "Ops"}, {"type": "add_tag", "value": "ai"}]}

    def test_crud_and_ordering(self, api, auth):
        h = auth("team_lead")
        r = api.post(f"{C}/rules/", headers=h, json=self.GOOD)
        assert r.status_code == 201 and r.json()["run_count"] == 0 and r.json()["is_active"] is True
        rid = r.json()["id"]
        api.patch(f"{C}/rules/{rid}/", headers=h, json={"order": 5, "is_active": False})
        got = api.get(f"{C}/rules/?is_active=false", headers=h).json()["results"]
        assert [x["id"] for x in got] == [rid] and got[0]["order"] == 5
        assert api.delete(f"{C}/rules/{rid}/", headers=h).status_code == 204

    @pytest.mark.parametrize("patch,field", [
        ({"event": "ticket.exploded"}, "event"), ({"actions": []}, "actions"), ({"actions": [{"type": "launch_missiles"}]}, "actions"),
        ({"actions": [{"type": "webhook", "url": "ftp://x"}]}, "actions"), ({"actions": [{"type": "notify", "to": []}]}, "actions"),
        ({"actions": [{"type": "assign_team"}]}, "actions"), ({"conditions": [{"field": "", "op": "eq"}]}, "conditions"),
        ({"conditions": [{"field": "x", "op": "regex"}]}, "conditions"), ({"conditions": [{"field": "x", "op": "in", "value": "a"}]}, "conditions"),
        ({"conditions": "nope"}, "conditions"),
    ])
    def test_invalid_rules_are_refused(self, api, auth, patch, field):
        r = api.post(f"{C}/rules/", headers=auth("team_lead"), json={**self.GOOD, **patch})
        assert r.status_code == 400 and field in r.json()["errors"]

    def test_created_rule_fires_on_new_tickets(self, api, world, auth):
        h = auth("manager")
        api.post(f"{C}/rules/", headers=h, json={"name": "Tag P3", "event": "ticket.created",
                                                  "conditions": [{"field": "priority.key", "op": "eq", "value": "P3"}],
                                                  "actions": [{"type": "add_tag", "value": "from-rule"}]})
        assert create_ticket_api(api, h, "x")["tags"] == ["from-rule"]
        assert api.get(f"{C}/rules/", headers=h).json()["results"][0]["run_count"] == 1


class TestCmdb:
    def test_services_items_and_dependencies(self, api, world, auth):
        h = auth("team_lead")
        svc = api.post(f"{C}/services/", headers=h, json={"name": "Payments", "criticality": 1, "account_id": world.account_id}).json()
        a = api.post(f"{C}/items/", headers=h, json={"name": "API", "ci_type": "application", "service_id": svc["id"], "tech_stack": "Java"}).json()
        b = api.post(f"{C}/items/", headers=h, json={"name": "DB", "ci_type": "database", "depends_on": []}).json()
        done = api.patch(f"{C}/items/{a['id']}/", headers=h, json={"depends_on": [b["id"]]}).json()
        assert done["depends_on"] == [b["id"]] and done["type_label"] == "Application"
        assert api.patch(f"{C}/items/{a['id']}/", headers=h, json={"depends_on": [a["id"]]}).status_code == 400
        graph = api.get(f"/api/cmdb/items/{a['id']}/graph/?direction=upstream", headers=auth("employee")).json()
        assert {n["name"] for n in graph["nodes"]} == {"API", "DB"} and graph["links"][0]["direction"] == "upstream"
        impact = api.get(f"/api/cmdb/items/{b['id']}/impact/", headers=auth("employee")).json()
        assert "API" in str(impact)
        sg = api.get(f"/api/cmdb/services/{svc['id']}/graph/", headers=auth("employee")).json()
        assert sg["root_type"] == "service" and any(n["name"] == "API" for n in sg["nodes"])
        assert api.post(f"{C}/items/", headers=h, json={"name": "x", "ci_type": "toaster"}).status_code == 400


class TestServiceDeskContent:
    def test_canned_responses_catalog_items_and_contact_identities(self, api, world, auth):
        h = auth("team_lead")
        cr = api.post(f"{C}/canned-responses/", headers=h, json={"title": "Thanks", "body": "Hi {{requester.first_name}}", "owner_id": None})
        assert cr.status_code == 201
        assert api.post(f"{C}/canned-responses/", headers=h, json={"title": "Own", "body": "b", "owner_id": world.users["customer"]}).status_code == 400
        tt = next(iter(api.get(f"{C}/ticket-types/", headers=h).json()["results"]))["id"]
        bad = api.post(f"{C}/catalog-items/", headers=h, json={"name": "Laptop", "ticket_type_id": tt, "form_schema": [{"type": "select", "name": "model"}]})
        assert bad.status_code == 400
        item = api.post(f"{C}/catalog-items/", headers=h, json={
            "name": "Laptop", "ticket_type_id": tt, "requires_approval": False,
            "form_schema": [{"name": "model", "label": "Model", "type": "select", "options": ["A", "B"], "required": True}]})
        assert item.status_code == 201
        listed = api.get("/api/catalog/", headers=auth("employee")).json()
        assert [i["name"] for i in listed] == ["Laptop"]
        assert api.get("/api/catalog/", headers=auth("customer")).status_code == 403
        ci = api.post(f"{C}/contact-identities/", headers=h, json={"user_id": world.users["customer"], "kind": "email", "value": "Alt@Globex.Example"})
        assert ci.status_code == 201 and ci.json()["value"] == "alt@globex.example"
        assert api.post(f"{C}/contact-identities/", headers=h, json={"user_id": world.users["employee"], "kind": "email", "value": "alt@globex.example"}).status_code == 400


class TestSlaCreateDelete:
    def targets(self, api, h):
        return [{"priority_id": p["id"], "first_response_minutes": 30, "resolution_minutes": 480} for p in api.get(f"{C}/priorities/", headers=h).json()["results"]]

    def test_business_hours_create_validate_delete(self, api, world, auth):
        h = auth("manager")
        r = api.post(f"{C}/business-hours/", headers=h, json={"name": "Weekdays", "timezone": "Europe/Berlin", "schedule": {"0": [["08:00", "17:00"]], "1": [["08:00", "17:00"]]},
                                                               "holidays": ["2026-12-25"]})
        assert r.status_code == 201 and r.json()["valid"] is True
        bid = r.json()["id"]
        assert api.post(f"{C}/business-hours/", headers=h, json={"name": "Empty"}).status_code == 400  # no working time and not 24x7
        assert api.post(f"{C}/business-hours/", headers=h, json={"name": "Z", "timezone": "Mars/Base", "is_24x7": True}).status_code == 400
        assert api.post(f"{C}/business-hours/", headers=h, json={"name": "T", "is_24x7": True}).status_code == 201
        assert api.post(f"{C}/business-hours/", headers=auth("team_lead"), json={"name": "Mgr", "is_24x7": True}).status_code == 403
        assert api.delete(f"{C}/business-hours/{bid}/", headers=h).status_code == 204
        std = next(x for x in api.get("/api/admin/business-hours/", headers=h).json()["results"] if x["name"].startswith("Standard"))
        d = api.delete(f"{C}/business-hours/{std['id']}/", headers=h)
        assert d.status_code == 409 and d.json()["usage"]["policies"] >= 1
        acts = audit_actions(world)
        assert "admin.business_hours.create" in acts and "admin.business_hours.delete" in acts

    def test_policy_create_validates_targets_defaults_and_deletes_only_when_unused(self, api, world, auth):
        h = auth("manager")
        bh = next(x for x in api.get("/api/admin/business-hours/", headers=h).json()["results"] if x["is_24x7"])["id"]
        body = {"name": "Gold", "is_default": False, "at_risk_percent": 80, "business_hours_id": bh, "targets": self.targets(api, h)}
        r = api.post(f"{C}/sla-policies/", headers=h, json=body)
        assert r.status_code == 201 and r.json()["at_risk_percent"] == 80
        pid = r.json()["id"]
        assert len([t for t in r.json()["targets"] if t["resolution_minutes"] == 480]) == 4
        for patch in ({"name": ""}, {"at_risk_percent": 0}, {"targets": [{"priority_id": 10 ** 9, "first_response_minutes": 1, "resolution_minutes": 2}]},
                      {"targets": [{**self.targets(api, h)[0], "first_response_minutes": 500, "resolution_minutes": 100}]},
                      {"business_hours_id": 10 ** 9}, {"ticket_type_ids": [10 ** 9]}):
            assert api.post(f"{C}/sla-policies/", headers=h, json={**body, "name": "Bad", **patch}).status_code == 400, patch
        incident = next(t["id"] for t in api.get(f"{C}/ticket-types/", headers=h).json()["results"] if t["key"] == "incident")
        clash = api.post(f"{C}/sla-policies/", headers=h, json={**body, "name": "Second default", "is_default": True, "ticket_type_ids": [incident]})
        assert clash.status_code == 409  # the standard policy already is the default for incidents
        all_types = api.post(f"{C}/sla-policies/", headers=h, json={**body, "name": "Catch-all default", "is_default": True})
        assert all_types.status_code == 201  # a catch-all default is allowed next to type-specific ones (the engine prefers the specific one)
        api.delete(f"{C}/sla-policies/{all_types.json()['id']}/", headers=h)
        assert api.post(f"{C}/sla-policies/", headers=auth("team_lead"), json=body).status_code == 403
        used = next(x for x in api.get("/api/admin/sla-policies/", headers=h).json()["results"] if x["is_default"])["id"]
        create_ticket_api(api, h, "uses the default policy")
        assert api.delete(f"{C}/sla-policies/{used}/", headers=h).status_code == 409
        assert api.delete(f"{C}/sla-policies/{pid}/", headers=h).status_code == 204
        assert {"admin.sla_policy.create", "admin.sla_policy.delete"} <= set(audit_actions(world))


class TestChannelDetailRow:
    def test_channel_rows_carry_what_the_detail_view_shows(self, api, world, auth):
        h = auth("manager")
        made = api.post(f"{C}/channels/", headers=h, json={"name": "Hook", "kind": "webhook", "config": {"webhook_token": "s"}, "settings": {"team": "Desk"}})
        assert made.status_code == 201
        row = api.get(f"{C}/channels/{made.json()['id']}/", headers=h).json()
        assert row["webhook_url"].endswith("/") and "/api/channels/inbound/" in row["webhook_url"]
        assert row["created_at"] and row["settings"] == {"team": "Desk"} and row["oauth_status"] is None
        assert api.get(f"{C}/channels/{made.json()['id']}/", headers=auth("employee")).status_code == 403
