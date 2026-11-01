from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app import analytics, tenant_config
from app.analytics import DashboardOut, MetricsOut
from app.auth import TenantContext, require
from app.db import Db

router = APIRouter(prefix="/api/tenants/{slug}", tags=["analytics"])

Reader = Annotated[TenantContext, require("cases:read")]
Days = Annotated[int, Query(ge=1, le=3650)]


@router.get("/dashboard")
async def dashboard(ctx: Reader, db: Db, days: Days = 30) -> DashboardOut:
    """Open work now, and SLA compliance of cases closed in the last `days` days."""
    config = await tenant_config.load(db, ctx.tenant.id)
    result = await analytics.dashboard(db, ctx, config, datetime.now(UTC), days)
    await ctx.audit(db, "dashboard.view", "dashboard", days=days)
    return result


@router.get("/metrics")
async def metrics(ctx: Reader, db: Db, days: Days = 90) -> MetricsOut:
    """Metrics over the cases opened in the last `days` days."""
    until = datetime.now(UTC)
    result = await analytics.metrics(db, ctx, until - timedelta(days=days), until)
    await ctx.audit(db, "metrics.view", "metrics", days=days)
    return result
