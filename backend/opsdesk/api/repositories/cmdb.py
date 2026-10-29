"""Direct typed SQL repository for CMDB models.

Executes parameterized, read-only SQL queries within transaction-local tenant context.
Never mutates records, avoids N+1 queries, and prevents cross-tenant data traversal.
"""
from typing import Dict, List, Optional, Tuple
import psycopg
from psycopg.rows import dict_row

from ..schemas.cmdb import (
    CI_TYPE_LABELS,
    CIImpactResponse,
    CIResponse,
    OpenTicketSummary,
    ServiceResponse,
)


class CMDBRepository:
    """Read-only data access repository for CMDB domain entities."""

    async def list_services(
        self,
        conn: psycopg.AsyncConnection,
        tenant_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ServiceResponse], int]:
        """Fetch paginated services for the given tenant, ordered by name ASC."""
        async with conn.cursor(row_factory=dict_row) as cur:
            # 1. Total count query
            await cur.execute(
                "SELECT COUNT(*) AS total FROM cmdb_service WHERE tenant_id = %s",
                (tenant_id,),
            )
            count_row = await cur.fetchone()
            total_count = count_row["total"] if count_row else 0

            # 2. Paginated data query
            await cur.execute(
                """
                SELECT id, name, description, account_id, owner_team_id, criticality, is_active
                FROM cmdb_service
                WHERE tenant_id = %s
                ORDER BY name ASC
                LIMIT %s OFFSET %s
                """,
                (tenant_id, limit, offset),
            )
            rows = await cur.fetchall()

            services = [
                ServiceResponse(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"] or "",
                    account=row["account_id"],
                    owner_team=row["owner_team_id"],
                    criticality=row["criticality"],
                    is_active=row["is_active"],
                )
                for row in rows
            ]
            return services, total_count

    async def list_configuration_items(
        self,
        conn: psycopg.AsyncConnection,
        tenant_id: int,
        account_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[CIResponse], int]:
        """Fetch paginated configuration items with dependencies, ordered by name ASC."""
        async with conn.cursor(row_factory=dict_row) as cur:
            # 1. Total count query
            if account_id is not None:
                await cur.execute(
                    "SELECT COUNT(*) AS total FROM cmdb_configurationitem WHERE tenant_id = %s AND account_id = %s",
                    (tenant_id, account_id),
                )
            else:
                await cur.execute(
                    "SELECT COUNT(*) AS total FROM cmdb_configurationitem WHERE tenant_id = %s",
                    (tenant_id,),
                )
            count_row = await cur.fetchone()
            total_count = count_row["total"] if count_row else 0

            # 2. Data query
            if account_id is not None:
                await cur.execute(
                    """
                    SELECT id, name, ci_type, environment, tech_stack, version, account_id,
                           service_id, owner_team_id, status, attributes
                    FROM cmdb_configurationitem
                    WHERE tenant_id = %s AND account_id = %s
                    ORDER BY name ASC
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_id, account_id, limit, offset),
                )
            else:
                await cur.execute(
                    """
                    SELECT id, name, ci_type, environment, tech_stack, version, account_id,
                           service_id, owner_team_id, status, attributes
                    FROM cmdb_configurationitem
                    WHERE tenant_id = %s
                    ORDER BY name ASC
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_id, limit, offset),
                )
            rows = await cur.fetchall()

            if not rows:
                return [], total_count

            # 3. Batch fetch depends_on for all CIs on current page (prevents N+1)
            ci_ids = [r["id"] for r in rows]
            await cur.execute(
                """
                SELECT from_configurationitem_id, to_configurationitem_id
                FROM cmdb_configurationitem_depends_on
                WHERE from_configurationitem_id = ANY(%s)
                ORDER BY to_configurationitem_id ASC
                """,
                (ci_ids,),
            )
            dep_rows = await cur.fetchall()

            depends_map: Dict[int, List[int]] = {cid: [] for cid in ci_ids}
            for d in dep_rows:
                depends_map[d["from_configurationitem_id"]].append(d["to_configurationitem_id"])

            items = [
                CIResponse(
                    id=row["id"],
                    name=row["name"],
                    ci_type=row["ci_type"],
                    type_label=CI_TYPE_LABELS.get(row["ci_type"], row["ci_type"].title()),
                    environment=row["environment"],
                    tech_stack=row["tech_stack"] or "",
                    version=row["version"] or "",
                    account=row["account_id"],
                    service=row["service_id"],
                    owner_team=row["owner_team_id"],
                    status=row["status"],
                    attributes=row["attributes"] if isinstance(row["attributes"], dict) else {},
                    depends_on=depends_map.get(row["id"], []),
                )
                for row in rows
            ]
            return items, total_count

    async def get_impact_analysis(
        self,
        conn: psycopg.AsyncConnection,
        ci_id: int,
        tenant_id: int,
        max_depth: int = 5,
    ) -> Optional[CIImpactResponse]:
        """Perform recursive dependency traversal to calculate impact and find open tickets.

        Returns None if root CI does not exist or does not belong to caller tenant.
        """
        async with conn.cursor(row_factory=dict_row) as cur:
            # 1. Verify existence and tenant ownership of the root CI
            await cur.execute(
                "SELECT id, tenant_id FROM cmdb_configurationitem WHERE id = %s AND tenant_id = %s",
                (ci_id, tenant_id),
            )
            root_ci = await cur.fetchone()
            if not root_ci:
                return None

            # 2. Recursive CTE finding all transitively impacted items (dependants up to max_depth)
            # Traversal logic: if A depends on B (from_id=A, to_id=B), then if B fails, A is impacted.
            # Starting at root_id=B, we follow to_configurationitem_id -> from_configurationitem_id.
            await cur.execute(
                """
                WITH RECURSIVE impact_cte AS (
                    SELECT
                        d.from_configurationitem_id AS ci_id,
                        1 AS depth,
                        ARRAY[d.to_configurationitem_id, d.from_configurationitem_id] AS path
                    FROM cmdb_configurationitem_depends_on d
                    JOIN cmdb_configurationitem c ON c.id = d.from_configurationitem_id
                    WHERE d.to_configurationitem_id = %(root_id)s
                      AND c.tenant_id = %(tenant_id)s
                      AND d.from_configurationitem_id != %(root_id)s

                    UNION

                    SELECT
                        d.from_configurationitem_id AS ci_id,
                        cte.depth + 1 AS depth,
                        path || d.from_configurationitem_id AS path
                    FROM impact_cte cte
                    JOIN cmdb_configurationitem_depends_on d ON d.to_configurationitem_id = cte.ci_id
                    JOIN cmdb_configurationitem c ON c.id = d.from_configurationitem_id
                    WHERE cte.depth <= %(max_depth)s
                      AND c.tenant_id = %(tenant_id)s
                      AND NOT (d.from_configurationitem_id = ANY(cte.path))
                )
                SELECT ci_id, depth FROM impact_cte;
                """,
                {"root_id": ci_id, "tenant_id": tenant_id, "max_depth": max_depth},
            )
            impacted_rows = await cur.fetchall()
            impacted_ids_set = {r["ci_id"] for r in impacted_rows if r["depth"] <= max_depth}
            impacted_ids = sorted(list(impacted_ids_set))
            beyond_depth_ids = {r["ci_id"] for r in impacted_rows if r["depth"] > max_depth}
            is_truncated = bool(beyond_depth_ids - impacted_ids_set)

            # 3. Fetch full representation for impacted items
            impacted_cis: List[CIResponse] = []
            if impacted_ids:
                await cur.execute(
                    """
                    SELECT id, name, ci_type, environment, tech_stack, version, account_id,
                           service_id, owner_team_id, status, attributes
                    FROM cmdb_configurationitem
                    WHERE id = ANY(%s) AND tenant_id = %s
                    ORDER BY name ASC
                    """,
                    (impacted_ids, tenant_id),
                )
                ci_rows = await cur.fetchall()

                # Batch fetch depends_on for all impacted items
                await cur.execute(
                    """
                    SELECT from_configurationitem_id, to_configurationitem_id
                    FROM cmdb_configurationitem_depends_on
                    WHERE from_configurationitem_id = ANY(%s)
                    ORDER BY to_configurationitem_id ASC
                    """,
                    (impacted_ids,),
                )
                dep_rows = await cur.fetchall()

                depends_map: Dict[int, List[int]] = {cid: [] for cid in impacted_ids}
                for d in dep_rows:
                    depends_map[d["from_configurationitem_id"]].append(d["to_configurationitem_id"])

                impacted_cis = [
                    CIResponse(
                        id=row["id"],
                        name=row["name"],
                        ci_type=row["ci_type"],
                        type_label=CI_TYPE_LABELS.get(row["ci_type"], row["ci_type"].title()),
                        environment=row["environment"],
                        tech_stack=row["tech_stack"] or "",
                        version=row["version"] or "",
                        account=row["account_id"],
                        service=row["service_id"],
                        owner_team=row["owner_team_id"],
                        status=row["status"],
                        attributes=row["attributes"] if isinstance(row["attributes"], dict) else {},
                        depends_on=depends_map.get(row["id"], []),
                    )
                    for row in ci_rows
                ]

            # 4. Open tickets against root CI or any impacted CIs
            all_target_ci_ids = [ci_id] + impacted_ids
            await cur.execute(
                """
                SELECT t.id, t.number, t.subject
                FROM tickets_ticket t
                JOIN workflow_state s ON s.id = t.state_id
                WHERE t.tenant_id = %s
                  AND t.ci_id = ANY(%s)
                  AND s.category NOT IN ('resolved', 'closed', 'cancelled')
                ORDER BY t.id ASC
                """,
                (tenant_id, all_target_ci_ids),
            )
            ticket_rows = await cur.fetchall()
            open_tickets = [
                OpenTicketSummary(
                    id=row["id"],
                    number=str(row["number"]),
                    subject=str(row["subject"]),
                )
                for row in ticket_rows
            ]

            return CIImpactResponse(impacted=impacted_cis, open_tickets=open_tickets, truncated=is_truncated)


cmdb_repo = CMDBRepository()
