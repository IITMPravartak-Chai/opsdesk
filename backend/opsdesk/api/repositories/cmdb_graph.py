"""CMDB dependency graphs (CI and service), read-only. Call inside the caller's tenant session; every relation is
also filtered to the item's own tenant."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ...db.models import CIDependency, ConfigurationItem, Service

DIRECTIONS = ("upstream", "downstream", "both")
CRITICALITY = {1: "Mission critical", 2: "High", 3: "Medium", 4: "Low"}


def clamp_params(depth, direction):
    """Depth 1-5 (default 2), direction upstream/downstream/both."""
    try:
        depth = int(depth)
    except (ValueError, TypeError):
        depth = 2
    direction = (direction or "both").lower()
    return max(1, min(depth, 5)), direction if direction in DIRECTIONS else "both"


class _Graph:
    def __init__(self):
        self.nodes: dict = {}
        self.links: list = []
        self._seen: set = set()

    def link(self, source_id, target_id, relation, link_dir):
        key = (source_id, target_id, relation)
        if key not in self._seen:
            self._seen.add(key)
            self.links.append({"source": source_id, "target": target_id, "relation": relation, "direction": link_dir})

    def walk(self, session: Session, start, depth, tenant_id, link_dir, serialize):
        """Breadth-first over depends_on (upstream) or dependants (downstream), depth hops."""
        frontier, visited = list(start), {c.id for c in start}
        for _ in range(depth):
            nxt = []
            for curr in frontier:
                if link_dir == "upstream":  # curr depends on dep
                    stmt = (select(ConfigurationItem).join(CIDependency, CIDependency.to_configurationitem_id == ConfigurationItem.id)
                            .where(CIDependency.from_configurationitem_id == curr.id))
                else:  # dep depends on curr
                    stmt = (select(ConfigurationItem).join(CIDependency, CIDependency.from_configurationitem_id == ConfigurationItem.id)
                            .where(CIDependency.to_configurationitem_id == curr.id))
                stmt = stmt.where(ConfigurationItem.tenant_id == tenant_id).options(
                    joinedload(ConfigurationItem.service), joinedload(ConfigurationItem.owner_team)).order_by(ConfigurationItem.name)
                for dep in session.scalars(stmt).unique():
                    if link_dir == "upstream":
                        self.link(curr.id, dep.id, "depends_on", "upstream")
                    else:
                        self.link(dep.id, curr.id, "depends_on", "downstream")
                    self.nodes.setdefault(dep.id, serialize(dep))
                    if dep.id not in visited:
                        visited.add(dep.id)
                        nxt.append(dep)
            frontier = nxt


def _ci_node(item, service=None):
    srv = service or item.service
    return {
        "id": item.id, "name": item.name, "ci_type": item.ci_type, "type_label": item.type_label,
        "environment": item.environment, "tech_stack": item.tech_stack, "version": item.version, "status": item.status,
        "service": {"id": srv.id, "name": srv.name, "criticality": srv.criticality} if srv else None,
        "owner_team": item.owner_team.name if item.owner_team else None,
    }


def ci_graph(session: Session, ci_id: int, tenant_id: int, depth, direction) -> Optional[dict]:
    ci = session.scalars(select(ConfigurationItem).options(joinedload(ConfigurationItem.service),
                                                           joinedload(ConfigurationItem.owner_team))
                         .where(ConfigurationItem.id == ci_id, ConfigurationItem.tenant_id == tenant_id)).first()
    if ci is None:
        return None
    depth, direction = clamp_params(depth, direction)
    g = _Graph()
    g.nodes[ci.id] = _ci_node(ci)
    if direction in ("upstream", "both"):
        g.walk(session, [ci], depth, tenant_id, "upstream", _ci_node)
    if direction in ("downstream", "both"):
        g.walk(session, [ci], depth, tenant_id, "downstream", _ci_node)
    return {"root_id": ci.id, "root_type": "ci", "depth": depth, "direction": direction,
            "nodes": list(g.nodes.values()), "links": g.links}


def service_graph(session: Session, service_id: int, tenant_id: int, depth, direction) -> Optional[dict]:
    srv = session.scalars(select(Service).options(joinedload(Service.owner_team)).where(
        Service.id == service_id, Service.tenant_id == tenant_id)).first()
    if srv is None:
        return None
    depth, direction = clamp_params(depth, direction)
    root = f"srv-{srv.id}"
    g = _Graph()
    g.nodes[root] = {
        "id": root, "name": srv.name, "type": "service", "type_label": "Service", "criticality": srv.criticality,
        "criticality_label": CRITICALITY.get(srv.criticality, str(srv.criticality)),
        "owner_team": srv.owner_team.name if srv.owner_team else None, "is_active": srv.is_active,
    }
    as_service_ci = lambda item: _ci_node(item, service=srv)  # noqa: E731 (every node is labelled with this service)
    direct = list(session.scalars(select(ConfigurationItem).options(joinedload(ConfigurationItem.owner_team))
                                  .where(ConfigurationItem.service_id == srv.id, ConfigurationItem.tenant_id == tenant_id)
                                  .order_by(ConfigurationItem.name)).unique())
    for ci in direct:
        g.nodes[ci.id] = as_service_ci(ci)
        g.link(root, ci.id, "provides", "downstream")
    if depth > 1 and direct:
        if direction in ("upstream", "both"):
            g.walk(session, direct, depth - 1, tenant_id, "upstream", as_service_ci)
        if direction in ("downstream", "both"):
            g.walk(session, direct, depth - 1, tenant_id, "downstream", as_service_ci)
    return {"root_id": root, "root_type": "service", "depth": depth, "direction": direction,
            "nodes": list(g.nodes.values()), "links": g.links}
