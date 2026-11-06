"""Direct typed SQL repository for Tickets and related entities.

Executes parameterized, tenant-isolated SQL queries within transaction-local tenant context.
Uses opsdesk.services.tickets for domain mutations to ensure zero business logic drift.
"""
from datetime import date
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

from ...db.models.core import OWN_TICKET_ROLES, PRIVILEGED_ROLES, STAFF_ROLES, effective_roles

from ..schemas.tickets import (
    AccountRef,
    ApprovalRef,
    AttachmentRef,
    AuthorRef,
    CategoryRef,
    CIRef,
    CannedResponseItem,
    EventResponse,
    InteractionResponse,
    PaginatedTicketResponse,
    PriorityRef,
    RelatedTicketRef,
    ServiceRef,
    StateRef,
    TeamRef,
    TicketCountsResponse,
    TicketDetailResponse,
    TicketLinkRef,
    TicketListItem,
    TransitionRef,
    TypeRef,
    UserRef,
)
from ..security import AuthenticatedUser


ORDERINGS_SQL = {
    "created": "t.created_at DESC, t.id DESC",
    "updated": "t.updated_at DESC, t.id DESC",
    "due": "t.resolution_due_at ASC NULLS LAST, t.id DESC",
    "priority": "p.level ASC, t.id DESC",
    "oldest": "t.created_at ASC, t.id DESC",
}


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


class TicketRepository:
    """Read-only and mutation repository for tickets domain."""

    async def _get_agent_team_accounts(self, conn: psycopg.AsyncConnection, user_id: int) -> Tuple[bool, List[int]]:
        """Check if agent has teams with account restrictions.

        Returns (restricted, account_ids).
        """
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT tm.team_id,
                       EXISTS (SELECT 1 FROM core_team_accounts ta WHERE ta.team_id = tm.team_id) AS has_accounts
                FROM core_team_members tm
                WHERE tm.user_id = %s
                """,
                (user_id,),
            )
            teams = await cur.fetchall()
            if not teams:
                return False, []
            # If any team has NO account restrictions, agent can see all accounts
            if any(not r["has_accounts"] for r in teams):
                return False, []

            await cur.execute(
                """
                SELECT DISTINCT ta.account_id
                FROM core_team_accounts ta
                JOIN core_team_members tm ON tm.team_id = ta.team_id
                WHERE tm.user_id = %s
                """,
                (user_id,),
            )
            rows = await cur.fetchall()
            return True, [r["account_id"] for r in rows]

    async def build_visibility_sql(
        self,
        conn: psycopg.AsyncConnection,
        user: AuthenticatedUser,
        params: dict,
    ) -> str:
        """Construct WHERE predicate for row-level ticket visibility."""
        params["tenant_id"] = user.tenant_id
        if user.role in PRIVILEGED_ROLES:
            return "t.tenant_id = %(tenant_id)s"

        if user.role in OWN_TICKET_ROLES:
            params["own_user_id"] = user.user_id
            return """
                t.tenant_id = %(tenant_id)s AND (
                    t.assignee_id = %(own_user_id)s
                    OR t.requester_id = %(own_user_id)s
                    OR t.created_by_id = %(own_user_id)s
                )
            """

        if user.is_agent:
            restricted, acc_ids = await self._get_agent_team_accounts(conn, user.user_id)
            if not restricted:
                return "t.tenant_id = %(tenant_id)s"
            params["agent_user_id"] = user.user_id
            params["agent_acc_ids"] = acc_ids
            return """
                t.tenant_id = %(tenant_id)s AND (
                    t.account_id = ANY(%(agent_acc_ids)s)
                    OR t.account_id IS NULL
                    OR t.assignee_id = %(agent_user_id)s
                )
            """

        # Requesters: the same rules as services/visibility.py (Pre-U6 closure, B).
        params["cust_user_id"] = user.user_id
        own_or_watching = """(
                    t.requester_id = %(cust_user_id)s
                    OR EXISTS (SELECT 1 FROM tickets_ticket_watchers tw WHERE tw.ticket_id = t.id AND tw.user_id = %(cust_user_id)s)
                )"""
        # Check tenant setting customer_sees_account_tickets
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT settings FROM core_tenant WHERE id = %s",
                (user.tenant_id,),
            )
            row = await cur.fetchone()
            tenant_settings = row["settings"] if row and isinstance(row["settings"], dict) else {}
            see_account = tenant_settings.get("customer_sees_account_tickets", True)

            await cur.execute(
                "SELECT account_id FROM core_user WHERE id = %s",
                (user.user_id,),
            )
            u_row = await cur.fetchone()
            account_id = u_row["account_id"] if u_row else None

        params["cust_account_id"] = account_id
        # A customer with an account never sees another account's tickets, even ones they requested
        # or watch; with the tenant setting on they see all of their own account's tickets.
        if account_id and see_account:
            return "t.tenant_id = %(tenant_id)s AND t.account_id = %(cust_account_id)s"
        if account_id:
            return f"t.tenant_id = %(tenant_id)s AND t.account_id = %(cust_account_id)s AND {own_or_watching}"
        return f"t.tenant_id = %(tenant_id)s AND {own_or_watching}"

    async def list_tickets(
        self,
        conn: psycopg.AsyncConnection,
        user: AuthenticatedUser,
        filters: dict,
        limit: int = 50,
        offset: int = 0,
        ordering: str = "updated",
    ) -> Tuple[List[TicketListItem], int]:
        """Fetch paginated tickets with filtering, matching DRF contract."""
        params: Dict[str, Any] = {}
        visibility_where = await self.build_visibility_sql(conn, user, params)
        where_clauses = [visibility_where]

        # 1. Search filter q
        if q := filters.get("q"):
            q_clean = q.strip()
            if re.fullmatch(r"[A-Za-z]{2,6}-\d+", q_clean):
                where_clauses.append("t.number ILIKE %(search_q)s")
                params["search_q"] = q_clean
            else:
                where_clauses.append("(t.subject ILIKE %(search_pat)s OR t.description ILIKE %(search_pat)s OR t.number ILIKE %(search_pat)s)")
                params["search_pat"] = f"%{q_clean}%"

        # 2. State & Category filters
        if sc := filters.get("state_category"):
            where_clauses.append("s.category = ANY(%(state_cat)s)")
            params["state_cat"] = sc.split(",")

        if st := filters.get("state"):
            try:
                where_clauses.append("s.id = ANY(%(state_ids)s)")
                params["state_ids"] = [int(x) for x in st.split(",") if x.strip().isdigit()]
            except Exception:
                pass

        if pr := filters.get("priority"):
            where_clauses.append("p.key = ANY(%(pri_keys)s)")
            params["pri_keys"] = pr.split(",")

        # 3. Assignee filter
        if v := filters.get("assignee"):
            if v == "me":
                where_clauses.append("t.assignee_id = %(filter_user_id)s")
                params["filter_user_id"] = user.user_id
            elif v == "none":
                where_clauses.append("t.assignee_id IS NULL")
            elif v.isdigit():
                where_clauses.append("t.assignee_id = %(filter_assignee_id)s")
                params["filter_assignee_id"] = int(v)

        if tm := filters.get("team"):
            if tm.isdigit():
                where_clauses.append("t.team_id = %(filter_team_id)s")
                params["filter_team_id"] = int(tm)

        if ac := filters.get("account"):
            if ac.isdigit():
                where_clauses.append("t.account_id = %(filter_account_id)s")
                params["filter_account_id"] = int(ac)

        if tp := filters.get("type"):
            where_clauses.append("tt.key = ANY(%(filter_types)s)")
            params["filter_types"] = tp.split(",")

        if sla := filters.get("sla_status"):
            where_clauses.append("t.sla_status = ANY(%(filter_sla)s)")
            params["filter_sla"] = sla.split(",")

        if src := filters.get("source"):
            where_clauses.append("t.source = ANY(%(filter_source)s)")
            params["filter_source"] = src.split(",")

        if filters.get("major") == "1":
            where_clauses.append("t.is_major_incident = true")

        if filters.get("mine") == "1":
            where_clauses.append("t.requester_id = %(filter_mine_id)s")
            params["filter_mine_id"] = user.user_id

        if filters.get("open") == "1":
            where_clauses.append("s.category NOT IN ('resolved', 'closed', 'cancelled')")

        where_sql = " AND ".join(where_clauses)
        order_sql = ORDERINGS_SQL.get(ordering, ORDERINGS_SQL["updated"])

        async with conn.cursor(row_factory=dict_row) as cur:
            # Count query
            count_query = f"""
                SELECT COUNT(*) AS total
                FROM tickets_ticket t
                JOIN workflow_tickettype tt ON tt.id = t.ticket_type_id
                JOIN workflow_state s ON s.id = t.state_id
                LEFT JOIN workflow_priority p ON p.id = t.priority_id
                WHERE {where_sql}
            """
            await cur.execute(count_query, params)
            count_row = await cur.fetchone()
            total_count = count_row["total"] if count_row else 0

            # Data query
            params["limit"] = limit
            params["offset"] = offset
            data_query = f"""
                SELECT
                    t.id, t.number, t.subject, t.source, t.sla_status,
                    t.response_due_at, t.resolution_due_at, t.first_responded_at,
                    t.is_major_incident, t.tags, t.created_at, t.updated_at, t.resolved_at,
                    tt.key AS type_key, tt.name AS type_name,
                    s.id AS state_id, s.key AS state_key, s.name AS state_name,
                    s.category AS state_category, s.color AS state_color,
                    p.id AS pri_id, p.key AS pri_key, p.name AS pri_name,
                    p.level AS pri_level, p.color AS pri_color,
                    a.id AS acc_id, a.name AS acc_name, a.code AS acc_code,
                    req.id AS req_id, COALESCE(NULLIF(TRIM(CONCAT(req.first_name, ' ', req.last_name)), ''), req.email) AS req_name,
                    ass.id AS ass_id, COALESCE(NULLIF(TRIM(CONCAT(ass.first_name, ' ', ass.last_name)), ''), ass.email) AS ass_name,
                    tm.id AS tm_id, tm.name AS tm_name
                FROM tickets_ticket t
                JOIN workflow_tickettype tt ON tt.id = t.ticket_type_id
                JOIN workflow_state s ON s.id = t.state_id
                LEFT JOIN workflow_priority p ON p.id = t.priority_id
                LEFT JOIN core_account a ON a.id = t.account_id
                LEFT JOIN core_user req ON req.id = t.requester_id
                LEFT JOIN core_user ass ON ass.id = t.assignee_id
                LEFT JOIN core_team tm ON tm.id = t.team_id
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT %(limit)s OFFSET %(offset)s
            """
            await cur.execute(data_query, params)
            rows = await cur.fetchall()

            items = [
                TicketListItem(
                    id=r["id"],
                    number=r["number"],
                    subject=r["subject"],
                    type=TypeRef(key=r["type_key"], name=r["type_name"]),
                    state=StateRef(
                        id=r["state_id"],
                        key=r["state_key"],
                        name=r["state_name"],
                        category=r["state_category"],
                        color=r["state_color"],
                    ),
                    priority=PriorityRef(
                        id=r["pri_id"],
                        key=r["pri_key"],
                        name=r["pri_name"],
                        level=r["pri_level"],
                        color=r["pri_color"],
                    ) if r["pri_id"] else None,
                    account=AccountRef(id=r["acc_id"], name=r["acc_name"], code=r["acc_code"]) if r["acc_id"] else None,
                    requester=UserRef(id=r["req_id"], name=r["req_name"]) if r["req_id"] else None,
                    assignee=UserRef(id=r["ass_id"], name=r["ass_name"]) if r["ass_id"] else None,
                    team=TeamRef(id=r["tm_id"], name=r["tm_name"]) if r["tm_id"] else None,
                    source=r["source"],
                    sla_status=r["sla_status"],
                    response_due_at=_iso(r["response_due_at"]),
                    resolution_due_at=_iso(r["resolution_due_at"]),
                    first_responded_at=_iso(r["first_responded_at"]),
                    is_major_incident=r["is_major_incident"],
                    tags=r["tags"] or [],
                    created_at=_iso(r["created_at"]),
                    updated_at=_iso(r["updated_at"]),
                    resolved_at=_iso(r["resolved_at"]),
                )
                for r in rows
            ]
            return items, total_count

    async def get_counts(self, conn: psycopg.AsyncConnection, user: AuthenticatedUser) -> TicketCountsResponse:
        """Fetch badge counts matching TicketViewSet.counts."""
        params: Dict[str, Any] = {}
        visibility_where = await self.build_visibility_sql(conn, user, params)

        async with conn.cursor(row_factory=dict_row) as cur:
            if not user.is_agent:
                query = f"""
                    SELECT COUNT(*) AS total_open
                    FROM tickets_ticket t
                    JOIN workflow_state s ON s.id = t.state_id
                    WHERE {visibility_where}
                      AND s.category NOT IN ('resolved', 'closed', 'cancelled')
                """
                await cur.execute(query, params)
                row = await cur.fetchone()
                return TicketCountsResponse(open=row["total_open"] if row else 0)

            params["agent_user_id"] = user.user_id
            query = f"""
                SELECT
                    COUNT(*) FILTER (WHERE s.category NOT IN ('resolved', 'closed', 'cancelled')) AS total_open,
                    COUNT(*) FILTER (WHERE s.category NOT IN ('resolved', 'closed', 'cancelled') AND t.assignee_id = %(agent_user_id)s) AS mine,
                    COUNT(*) FILTER (WHERE s.category NOT IN ('resolved', 'closed', 'cancelled') AND t.assignee_id IS NULL) AS unassigned,
                    COUNT(*) FILTER (WHERE s.category NOT IN ('resolved', 'closed', 'cancelled') AND t.sla_status IN ('at_risk', 'breached')) AS sla,
                    COUNT(*) FILTER (WHERE s.category NOT IN ('resolved', 'closed', 'cancelled') AND t.is_major_incident = true) AS major
                FROM tickets_ticket t
                JOIN workflow_state s ON s.id = t.state_id
                WHERE {visibility_where}
            """
            await cur.execute(query, params)
            row = await cur.fetchone()
            if not row:
                return TicketCountsResponse(open=0)
            return TicketCountsResponse(
                mine=row["mine"],
                unassigned=row["unassigned"],
                sla=row["sla"],
                major=row["major"],
                open=row["total_open"],
            )

    @staticmethod
    async def customer_visible_fields(cur, ticket_type_id: int) -> set:
        """Custom-field keys the ticket type shows to requesters (CustomField.visible_to_customer)."""
        await cur.execute(
            "SELECT key FROM workflow_customfield WHERE ticket_type_id = %s AND visible_to_customer",
            (ticket_type_id,),
        )
        return {row["key"] for row in await cur.fetchall()}

    async def get_ticket_detail(
        self,
        conn: psycopg.AsyncConnection,
        ticket_id: int,
        user: AuthenticatedUser,
    ) -> Optional[TicketDetailResponse]:
        """Fetch full ticket detail representation matching DRF TicketDetailSerializer."""
        params: Dict[str, Any] = {"target_id": ticket_id}
        visibility_where = await self.build_visibility_sql(conn, user, params)

        async with conn.cursor(row_factory=dict_row) as cur:
            query = f"""
                SELECT
                    t.id, t.number, t.subject, t.description, t.source, t.sla_status,
                    t.impact, t.urgency, t.custom_fields, t.resolution_code, t.resolution_notes,
                    t.closed_at, t.response_breached, t.resolution_breached,
                    t.response_due_at, t.resolution_due_at, t.first_responded_at,
                    t.is_major_incident, t.tags, t.created_at, t.updated_at, t.resolved_at,
                    t.ai_summary, t.ai_triage, t.ticket_type_id,
                    tt.key AS type_key, tt.name AS type_name,
                    s.id AS state_id, s.key AS state_key, s.name AS state_name,
                    s.category AS state_category, s.color AS state_color, s.workflow_id,
                    p.id AS pri_id, p.key AS pri_key, p.name AS pri_name,
                    p.level AS pri_level, p.color AS pri_color,
                    a.id AS acc_id, a.name AS acc_name, a.code AS acc_code,
                    req.id AS req_id, COALESCE(NULLIF(TRIM(CONCAT(req.first_name, ' ', req.last_name)), ''), req.email) AS req_name, req.email AS req_email,
                    req.phone AS req_phone, req.job_title AS req_job_title,
                    ass.id AS ass_id, COALESCE(NULLIF(TRIM(CONCAT(ass.first_name, ' ', ass.last_name)), ''), ass.email) AS ass_name,
                    tm.id AS tm_id, tm.name AS tm_name,
                    cat.id AS cat_id, cat.name AS cat_name,
                    srv.id AS srv_id, srv.name AS srv_name,
                    ci.id AS ci_id, ci.name AS ci_name, ci.ci_type, ci.environment, ci.version, ci.tech_stack,
                    sla_p.name AS sla_policy_name,
                    t.major_incident_status, t.post_incident_review,
                    ic.id AS ic_id, COALESCE(NULLIF(TRIM(CONCAT(ic.first_name, ' ', ic.last_name)), ''), ic.email) AS ic_name,
                    par.id AS par_id, par.number AS par_number, par.subject AS par_subject
                FROM tickets_ticket t
                JOIN workflow_tickettype tt ON tt.id = t.ticket_type_id
                JOIN workflow_state s ON s.id = t.state_id
                LEFT JOIN workflow_priority p ON p.id = t.priority_id
                LEFT JOIN core_account a ON a.id = t.account_id
                LEFT JOIN core_user req ON req.id = t.requester_id
                LEFT JOIN core_user ass ON ass.id = t.assignee_id
                LEFT JOIN core_team tm ON tm.id = t.team_id
                LEFT JOIN workflow_category cat ON cat.id = t.category_id
                LEFT JOIN cmdb_service srv ON srv.id = t.service_id
                LEFT JOIN cmdb_configurationitem ci ON ci.id = t.ci_id
                LEFT JOIN workflow_slapolicy sla_p ON sla_p.id = t.sla_policy_id
                LEFT JOIN core_user ic ON ic.id = t.incident_commander_id
                LEFT JOIN tickets_ticket par ON par.id = t.parent_id
                WHERE t.id = %(target_id)s AND {visibility_where}
            """
            await cur.execute(query, params)
            r = await cur.fetchone()
            if not r:
                return None

            # 1. Transitions: the same rules as services.workflow.available_transitions() for staff. A transition
            # without a source status applies from any status except closed/cancelled; it never targets the current
            # status; allowed_roles (empty = any staff role) must include the caller's role.
            if user.is_agent:
                await cur.execute(
                    """
                    SELECT tr.id, tr.name, st_to.name AS to_name, st_to.category AS to_category,
                           tr.requires_comment, tr.required_fields, tr.requires_approval
                    FROM workflow_transition tr
                    JOIN workflow_state st_to ON st_to.id = tr.to_state_id
                    WHERE tr.workflow_id = %(wf)s AND tr.to_state_id <> %(st)s
                      AND (tr.from_state_id = %(st)s
                           OR (tr.from_state_id IS NULL AND %(cat)s NOT IN ('closed', 'cancelled')))
                      AND (jsonb_array_length(COALESCE(tr.allowed_roles, '[]'::jsonb)) = 0
                           OR COALESCE(tr.allowed_roles, '[]'::jsonb) ?| %(roles)s::text[])
                    ORDER BY tr."order" ASC, tr.id ASC
                    """,
                    {"wf": r["workflow_id"], "st": r["state_id"], "cat": r["state_category"], "roles": list(effective_roles(user.role))},
                )
            else:
                # Requesters see only what they may perform: workflow.engine.available_transitions()
                # with _role_ok() for a non-staff user (customer_allowed).
                await cur.execute(
                    """
                    SELECT tr.id, tr.name, st_to.name AS to_name, st_to.category AS to_category,
                           tr.requires_comment, tr.required_fields, tr.requires_approval
                    FROM workflow_transition tr
                    JOIN workflow_state st_to ON st_to.id = tr.to_state_id
                    WHERE tr.workflow_id = %(wf)s AND tr.customer_allowed AND tr.to_state_id <> %(st)s
                      AND (tr.from_state_id = %(st)s
                           OR (tr.from_state_id IS NULL AND %(cat)s NOT IN ('closed', 'cancelled')))
                    ORDER BY tr."order" ASC, tr.id ASC
                    """,
                    {"wf": r["workflow_id"], "st": r["state_id"], "cat": r["state_category"]},
                )
            trans_rows = await cur.fetchall()
            transitions = [
                TransitionRef(
                    id=tr["id"],
                    name=tr["name"],
                    to=tr["to_name"],
                    to_category=tr["to_category"],
                    requires_comment=tr["requires_comment"],
                    required_fields=tr["required_fields"] or [],
                    requires_approval=tr["requires_approval"],
                )
                for tr in trans_rows
            ]

            # 2. Approvals
            await cur.execute(
                """
                SELECT ap.id, ap.status, ap.comment, ap.decided_at,
                       u.id AS app_user_id, COALESCE(NULLIF(TRIM(CONCAT(u.first_name, ' ', u.last_name)), ''), u.email) AS app_user_name
                FROM tickets_approval ap
                JOIN core_user u ON u.id = ap.approver_id
                WHERE ap.ticket_id = %s
                ORDER BY ap.id ASC
                """,
                (ticket_id,),
            )
            app_rows = await cur.fetchall()
            approvals = [
                ApprovalRef(
                    id=a["id"],
                    approver={"id": a["app_user_id"], "name": a["app_user_name"]},
                    status=a["status"],
                    comment=a["comment"],
                    decided_at=_iso(a["decided_at"]),
                )
                for a in app_rows
            ]

            # 3. Attachments (unattached to interactions)
            await cur.execute(
                """
                SELECT id, filename, content_type, size, scan_status, created_at, interaction_id
                FROM tickets_attachment
                WHERE ticket_id = %s AND interaction_id IS NULL
                ORDER BY id ASC
                """,
                (ticket_id,),
            )
            att_rows = await cur.fetchall()
            attachments = [
                AttachmentRef(
                    id=at["id"],
                    filename=at["filename"],
                    content_type=at["content_type"],
                    size=at["size"],
                    scan_status=at["scan_status"],
                    created_at=_iso(at["created_at"]),
                    interaction_id=at["interaction_id"],
                    url=f"/api/attachments/{at['id']}/download/",
                )
                for at in att_rows
            ]

            # 4. Links
            await cur.execute(
                """
                SELECT tl.link_type, tg.id, tg.number, tg.subject, s.name AS state_name
                FROM tickets_ticketlink tl
                JOIN tickets_ticket tg ON tg.id = tl.target_id
                JOIN workflow_state s ON s.id = tg.state_id
                WHERE tl.source_id = %s
                """,
                (ticket_id,),
            )
            out_links = await cur.fetchall()
            await cur.execute(
                """
                SELECT tl.link_type, src.id, src.number, src.subject, s.name AS state_name
                FROM tickets_ticketlink tl
                JOIN tickets_ticket src ON src.id = tl.source_id
                JOIN workflow_state s ON s.id = src.state_id
                WHERE tl.target_id = %s
                """,
                (ticket_id,),
            )
            in_links = await cur.fetchall()
            links = [
                TicketLinkRef(
                    type=l["link_type"],
                    id=l["id"],
                    number=l["number"],
                    subject=l["subject"],
                    state=l["state_name"],
                )
                for l in out_links
            ] + [
                TicketLinkRef(
                    type=f"{l['link_type']} (inverse)",
                    id=l["id"],
                    number=l["number"],
                    subject=l["subject"],
                    state=l["state_name"],
                )
                for l in in_links
            ]

            # 5. Children
            await cur.execute(
                """
                SELECT c.id, c.number, c.subject, s.name AS state_name
                FROM tickets_ticket c
                JOIN workflow_state s ON s.id = c.state_id
                WHERE c.parent_id = %s
                LIMIT 50
                """,
                (ticket_id,),
            )
            ch_rows = await cur.fetchall()
            children = [
                RelatedTicketRef(id=c["id"], number=c["number"], subject=c["subject"], state=c["state_name"])
                for c in ch_rows
            ]

            # 6. Time logged minutes
            await cur.execute(
                "SELECT COALESCE(SUM(minutes), 0) AS total_min FROM tickets_timeentry WHERE ticket_id = %s",
                (ticket_id,),
            )
            time_row = await cur.fetchone()
            total_minutes = time_row["total_min"] if time_row else 0

            # 7. Requester display logic
            requester_data = None
            if r["req_id"]:
                requester_data = UserRef(
                    id=r["req_id"],
                    name=r["req_name"],
                    email="" if (r["req_email"] or "").endswith(".invalid") else r["req_email"] if user.is_agent else None,
                    phone=r["req_phone"] if user.is_agent else None,
                    job_title=r["req_job_title"] if user.is_agent else None,
                )

            ai_data = None
            if user.is_agent and (r["ai_summary"] or r["ai_triage"]):  # staff-only, as in the reference
                ai_data = {
                    "summary": r["ai_summary"],
                    "triage": r["ai_triage"],
                }

            custom_fields = r["custom_fields"] if isinstance(r["custom_fields"], dict) else {}
            parent = RelatedTicketRef(id=r["par_id"], number=r["par_number"], subject=r["par_subject"]) if r["par_id"] else None
            if not user.is_agent:
                # Related tickets are listed only if the requester may see them, and custom fields
                # only if the ticket type marks them visible to requesters.
                related = {l.id for l in links} | {c.id for c in children} | ({parent.id} if parent else set())
                visible = set()
                if related:
                    rel_params = {**params, "related_ids": list(related)}
                    await cur.execute(
                        f"SELECT t.id FROM tickets_ticket t WHERE t.id = ANY(%(related_ids)s) AND {visibility_where}",
                        rel_params,
                    )
                    visible = {row["id"] for row in await cur.fetchall()}
                links = [l for l in links if l.id in visible]
                children = [c for c in children if c.id in visible]
                parent = parent if parent and parent.id in visible else None
                allowed = await self.customer_visible_fields(cur, r["ticket_type_id"])
                custom_fields = {k: v for k, v in custom_fields.items() if k in allowed}

            return TicketDetailResponse(
                id=r["id"],
                number=r["number"],
                subject=r["subject"],
                description=r["description"] or "",
                type=TypeRef(key=r["type_key"], name=r["type_name"]),
                state=StateRef(
                    id=r["state_id"],
                    key=r["state_key"],
                    name=r["state_name"],
                    category=r["state_category"],
                    color=r["state_color"],
                ),
                priority=PriorityRef(
                    id=r["pri_id"],
                    key=r["pri_key"],
                    name=r["pri_name"],
                    level=r["pri_level"],
                    color=r["pri_color"],
                ) if r["pri_id"] else None,
                account=AccountRef(id=r["acc_id"], name=r["acc_name"], code=r["acc_code"]) if r["acc_id"] else None,
                requester=requester_data,
                assignee=UserRef(id=r["ass_id"], name=r["ass_name"]) if r["ass_id"] else None,
                team=TeamRef(id=r["tm_id"], name=r["tm_name"]) if r["tm_id"] else None,
                category=CategoryRef(id=r["cat_id"], name=r["cat_name"]) if r["cat_id"] else None,
                service=ServiceRef(id=r["srv_id"], name=r["srv_name"]) if r["srv_id"] else None,
                ci=CIRef(
                    id=r["ci_id"],
                    name=r["ci_name"],
                    type=r["ci_type"],
                    environment=r["environment"],
                    version=r["version"],
                    tech_stack=r["tech_stack"],
                ) if r["ci_id"] else None,
                source=r["source"],
                sla_status=r["sla_status"],
                impact=r["impact"],
                urgency=r["urgency"],
                custom_fields=custom_fields,
                resolution_code=r["resolution_code"],
                resolution_notes=r["resolution_notes"],
                closed_at=_iso(r["closed_at"]),
                response_breached=r["response_breached"],
                resolution_breached=r["resolution_breached"],
                response_due_at=_iso(r["response_due_at"]),
                resolution_due_at=_iso(r["resolution_due_at"]),
                first_responded_at=_iso(r["first_responded_at"]),
                is_major_incident=r["is_major_incident"],
                tags=r["tags"] or [],
                created_at=_iso(r["created_at"]),
                updated_at=_iso(r["updated_at"]),
                resolved_at=_iso(r["resolved_at"]),
                sla_policy=r["sla_policy_name"],
                transitions=transitions,
                approvals=approvals,
                attachments=attachments,
                links=links,
                parent=parent,
                children=children,
                time_logged_minutes=total_minutes,
                ai=ai_data,
                # Major incident (U6): staff only, as the coordination workspace is staff-only.
                incident_commander=UserRef(id=r["ic_id"], name=r["ic_name"]) if (user.is_agent and r["ic_id"]) else None,
                major_incident_status=(r["major_incident_status"] or None) if user.is_agent else None,
                post_incident_review=(r["post_incident_review"] or None) if user.is_agent else None,
            )

    async def list_interactions(
        self,
        conn: psycopg.AsyncConnection,
        ticket_id: int,
        user: AuthenticatedUser,
    ) -> List[InteractionResponse]:
        """Fetch ticket interactions, filtering internal notes for customers."""
        params: Dict[str, Any] = {"target_id": ticket_id}
        visibility_where = await self.build_visibility_sql(conn, user, params)

        async with conn.cursor(row_factory=dict_row) as cur:
            # Verify ticket exists and is visible
            await cur.execute(
                f"SELECT id FROM tickets_ticket t WHERE t.id = %(target_id)s AND {visibility_where}",
                params,
            )
            if not await cur.fetchone():
                return []

            kind_filter = "" if user.is_agent else "AND it.kind = 'public'"
            query = f"""
                SELECT
                    it.id, it.kind, it.direction, it.body, it.source,
                    it.delivery_status, it.delivery_error, it.created_at,
                    u.id AS author_id, COALESCE(NULLIF(TRIM(CONCAT(u.first_name, ' ', u.last_name)), ''), u.email) AS author_name,
                    u.role AS author_role
                FROM tickets_interaction it
                LEFT JOIN core_user u ON u.id = it.author_id
                WHERE it.ticket_id = %s {kind_filter}
                ORDER BY it.id ASC
            """
            await cur.execute(query, (ticket_id,))
            rows = await cur.fetchall()

            if not rows:
                return []

            it_ids = [r["id"] for r in rows]
            await cur.execute(
                """
                SELECT id, filename, content_type, size, scan_status, created_at, interaction_id
                FROM tickets_attachment
                WHERE interaction_id = ANY(%s)
                ORDER BY id ASC
                """,
                (it_ids,),
            )
            att_rows = await cur.fetchall()
            att_map: Dict[int, List[AttachmentRef]] = {iid: [] for iid in it_ids}
            for at in att_rows:
                att_map[at["interaction_id"]].append(
                    AttachmentRef(
                        id=at["id"],
                        filename=at["filename"],
                        content_type=at["content_type"],
                        size=at["size"],
                        scan_status=at["scan_status"],
                        created_at=_iso(at["created_at"]),
                        interaction_id=at["interaction_id"],
                        url=f"/api/attachments/{at['id']}/download/",
                    )
                )

            items = [
                InteractionResponse(
                    id=r["id"],
                    kind=r["kind"],
                    direction=r["direction"],
                    body=r["body"],
                    author=AuthorRef(
                        id=r["author_id"],
                        name=r["author_name"] or ("System" if r["kind"] == "system" else "Customer"),
                        is_agent=r["author_role"] in STAFF_ROLES if r["author_role"] else False,
                    ) if r["author_id"] else AuthorRef(name="System" if r["kind"] == "system" else "Customer", is_agent=False),
                    source=r["source"],
                    delivery_status=r["delivery_status"],
                    delivery_error=r["delivery_error"],
                    created_at=_iso(r["created_at"]),
                    attachments=att_map.get(r["id"], []),
                )
                for r in rows
            ]
            return items

    async def list_canned_responses(
        self,
        conn: psycopg.AsyncConnection,
        user: AuthenticatedUser,
    ) -> List[CannedResponseItem]:
        """Fetch canned responses visible to the agent."""
        async with conn.cursor(row_factory=dict_row) as cur:
            # The CannedResponse model (and every migration) has no shortcut or category column;
            # selecting them made this route fail for everyone (U6 Workstream B). The response
            # keeps both keys, always null, so its shape is unchanged.
            await cur.execute(
                """
                SELECT id, title, body, (owner_id IS NULL) AS is_shared
                FROM tickets_cannedresponse
                WHERE tenant_id = %s AND (owner_id IS NULL OR owner_id = %s)
                ORDER BY title ASC
                """,
                (user.tenant_id, user.user_id),
            )
            rows = await cur.fetchall()
            return [
                CannedResponseItem(id=r["id"], title=r["title"], body=r["body"], is_shared=r["is_shared"])
                for r in rows
            ]

    async def render_canned_response(
        self,
        conn: psycopg.AsyncConnection,
        canned_id: int,
        ticket_id: int,
        user: AuthenticatedUser,
    ) -> Optional[str]:
        """Render template variables in canned response body for a specific ticket."""
        params: Dict[str, Any] = {"target_id": ticket_id}
        visibility_where = await self.build_visibility_sql(conn, user, params)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""
                SELECT t.number, t.subject, req.first_name AS req_first_name
                FROM tickets_ticket t
                LEFT JOIN core_user req ON req.id = t.requester_id
                WHERE t.id = %(target_id)s AND {visibility_where}
                """,
                params,
            )
            t_row = await cur.fetchone()
            if not t_row:
                return None

            await cur.execute(
                """
                SELECT body FROM tickets_cannedresponse
                WHERE id = %s AND tenant_id = %s AND (owner_id IS NULL OR owner_id = %s)
                """,
                (canned_id, user.tenant_id, user.user_id),
            )
            cr_row = await cur.fetchone()
            if not cr_row:
                return None

            await cur.execute(
                "SELECT first_name, email FROM core_user WHERE id = %s",
                (user.user_id,),
            )
            u_row = await cur.fetchone()
            agent_first = u_row["first_name"] or u_row["email"] if u_row else "Agent"

            ctx = {
                "ticket.number": t_row["number"],
                "ticket.subject": t_row["subject"],
                "requester.first_name": t_row["req_first_name"] or "there",
                "agent.first_name": agent_first,
            }
            body = re.sub(r"\{\{\s*([\w.]+)\s*\}\}", lambda m: str(ctx.get(m.group(1), m.group(0))), cr_row["body"])
            return body


ticket_repo = TicketRepository()
