"""Full-text search across cases, notes, observables, and evidence metadata."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import Integer, Text, case, cast, column, func, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import TenantContext

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Kind = Literal["case", "note", "observable", "evidence"]
# Plain-text excerpts: no markup comes back to be rendered.
HEADLINE = 'StartSel="",StopSel="",MaxWords=25,MinWords=10,MaxFragments=1'


class Hit(BaseModel):
    case_number: int
    case_title: str
    kind: Kind
    excerpt: str


class SavedSearchIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    query: Query


class SavedSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    query: str


async def run(db: AsyncSession, ctx: TenantContext, q: str, limit: int) -> list[Hit]:
    """Best matches first; only cases the caller can see are searched.

    The search_hits database function (migration 0011) finds and ranks the hits.
    """
    top = func.search_hits(q, "restricted_case" in ctx.permissions, limit).table_valued(
        column("case_number", Integer),
        column("case_title", Text),
        column("kind", Text),
        column("text", Text),
        column("rank"),
    )
    # Excerpts are built only for the hits returned: a common word can match many thousands of rows.
    english = cast("english", REGCONFIG)
    headline = func.ts_headline(
        english, top.c.text, func.websearch_to_tsquery(english, q), HEADLINE
    )
    excerpt = case((top.c.kind == "observable", top.c.text), else_=headline)
    rows = await db.execute(
        select(top.c.case_number, top.c.case_title, top.c.kind, excerpt.label("excerpt")).order_by(
            top.c.rank.desc(), top.c.case_number.desc()
        )
    )
    return [Hit.model_validate(row, from_attributes=True) for row in rows]
