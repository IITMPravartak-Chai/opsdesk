"""CMDB API routes.

Endpoints:
- GET /api/cmdb/services/
- GET /api/cmdb/items/
- GET /api/cmdb/items/{id}/impact/
- GET /api/cmdb/items/{id}/graph/, /api/cmdb/services/{id}/graph/ (U6)
"""
from typing import Optional
from urllib.parse import urlencode, parse_qs, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from ..repositories import cmdb_graph
from ..repositories.cmdb import cmdb_repo
from ..schemas.cmdb import (
    CIImpactResponse,
    CIResponse,
    PaginatedCIResponse,
    PaginatedServiceResponse,
    ServiceResponse,
)
from ..security import AuthenticatedUser, get_current_user
from ..tenant_context import tenant_transaction
from ..runtime import run_in_tenant

router = APIRouter(prefix="/api/cmdb", tags=["cmdb"])


def _build_pagination_url(request: Request, target_page: int) -> str:
    """Construct absolute pagination URL in the paginated list format the web client expects."""
    split = urlsplit(str(request.url))
    query_params = parse_qs(split.query)
    query_params["page"] = [str(target_page)]
    new_query = urlencode(query_params, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, new_query, split.fragment))


@router.get("/services/", response_model=PaginatedServiceResponse)
async def list_services(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List business and technical services for the caller's tenant."""
    offset = (page - 1) * page_size
    async with tenant_transaction(user) as conn:
        services, total_count = await cmdb_repo.list_services(
            conn=conn,
            tenant_id=user.tenant_id,
            limit=page_size,
            offset=offset,
        )

    if total_count > 0 and offset >= total_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid page.")
    if total_count == 0 and page > 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid page.")

    next_url = _build_pagination_url(request, page + 1) if (offset + page_size) < total_count else None
    prev_url = _build_pagination_url(request, page - 1) if page > 1 else None

    return PaginatedServiceResponse(
        count=total_count,
        next=next_url,
        previous=prev_url,
        results=services,
    )


@router.get("/services/{id}/", response_model=ServiceResponse)
async def get_service(
    id: int = Path(..., ge=1, description="Service ID"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve a single service."""
    async with tenant_transaction(user) as conn:
        services, _ = await cmdb_repo.list_services(
            conn=conn, tenant_id=user.tenant_id, limit=1000, offset=0
        )
    matching = [s for s in services if s.id == id]
    if not matching:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return matching[0]


@router.get("/items/", response_model=PaginatedCIResponse)
async def list_configuration_items(
    request: Request,
    account: Optional[int] = Query(None, description="Filter by account ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List configuration items (applications, AI models, platforms, etc.) for the caller's tenant."""
    offset = (page - 1) * page_size
    async with tenant_transaction(user) as conn:
        items, total_count = await cmdb_repo.list_configuration_items(
            conn=conn,
            tenant_id=user.tenant_id,
            account_id=account,
            limit=page_size,
            offset=offset,
        )

    if total_count > 0 and offset >= total_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid page.")
    if total_count == 0 and page > 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid page.")

    next_url = _build_pagination_url(request, page + 1) if (offset + page_size) < total_count else None
    prev_url = _build_pagination_url(request, page - 1) if page > 1 else None

    return PaginatedCIResponse(
        count=total_count,
        next=next_url,
        previous=prev_url,
        results=items,
    )


@router.get("/items/{id}/", response_model=CIResponse)
async def get_configuration_item(
    id: int = Path(..., ge=1, description="Configuration item ID"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve a single configuration item."""
    async with tenant_transaction(user) as conn:
        items, _ = await cmdb_repo.list_configuration_items(
            conn=conn, tenant_id=user.tenant_id, limit=1000, offset=0
        )
    matching = [item for item in items if item.id == id]
    if not matching:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return matching[0]


@router.get("/items/{id}/impact/", response_model=CIImpactResponse)
async def get_ci_impact(
    response: Response,
    id: int = Path(..., ge=1, description="Configuration item ID"),
    depth: int = Query(10, ge=1, le=50, description="Maximum dependency traversal depth"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Impact analysis: what transitively depends on this CI and which tickets are open against it."""
    async with tenant_transaction(user) as conn:
        result = await cmdb_repo.get_impact_analysis(
            conn=conn,
            ci_id=id,
            tenant_id=user.tenant_id,
            max_depth=depth,
        )

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    response.headers["X-Impact-Truncated"] = "true" if result.truncated else "false"
    return result


# ----------------------------------------------------------------------------- dependency graphs
# The CMDB explorer's graph view calls these.

@router.get("/items/{id}/graph/")
async def get_ci_graph(
    id: int = Path(..., ge=1, description="Configuration item ID"),
    depth: Optional[str] = Query("2", description="Traversal depth, 1-5"),
    direction: Optional[str] = Query("both", description="upstream, downstream or both"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Dependency graph around a configuration item (read-only, staff only)."""
    return await _graph(cmdb_graph.ci_graph, id, user, depth, direction)


@router.get("/services/{id}/graph/")
async def get_service_graph(
    id: int = Path(..., ge=1, description="Service ID"),
    depth: Optional[str] = Query("2", description="Traversal depth, 1-5"),
    direction: Optional[str] = Query("both", description="upstream, downstream or both"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Dependency graph of a business service and its configuration items (read-only, staff only)."""
    return await _graph(cmdb_graph.service_graph, id, user, depth, direction)


async def _graph(builder, id, user, depth, direction):
    result = await run_in_tenant(user, lambda s: builder(s, id, user.tenant_id, depth, direction), commit=False)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return result
