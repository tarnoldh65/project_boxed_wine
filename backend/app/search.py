"""Full-text search across cases, notes, observables, and evidence metadata."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import ColumnElement, Select, cast, func, literal, literal_column, select, union_all
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app import cases
from app.auth import TenantContext
from app.models import Case, CaseObservable, Evidence, Note, Observable

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


def _matches(table: str, config: str, q: str) -> tuple[ColumnElement[bool], ColumnElement[float]]:
    document = literal_column(f"{table}.search")
    query = func.websearch_to_tsquery(cast(config, REGCONFIG), q)
    return document.op("@@")(query), func.ts_rank(document, query)


def _headline(text: ColumnElement[str] | InstrumentedAttribute[str], q: str) -> ColumnElement[str]:
    english = cast("english", REGCONFIG)
    return func.ts_headline(english, text, func.websearch_to_tsquery(english, q), HEADLINE)


async def run(db: AsyncSession, ctx: TenantContext, q: str, limit: int) -> list[Hit]:
    """Best matches first; only cases the caller can see are searched."""
    visible = cases.visible(ctx).subquery()

    def hits(kind: Kind, excerpt: ColumnElement[str], match: ColumnElement[bool],
             rank: ColumnElement[float]) -> Select:  # fmt: skip
        return select(
            visible.c.number.label("case_number"),
            visible.c.title.label("case_title"),
            literal(kind).label("kind"),
            excerpt.label("excerpt"),
            rank.label("rank"),
        ).where(match)

    case_match, case_rank = _matches("cases", "english", q)
    note_match, note_rank = _matches("notes", "english", q)
    evidence_match, evidence_rank = _matches("evidence", "english", q)
    observable_match, observable_rank = _matches("observables", "simple", q)
    queries = [
        hits("case", _headline(Case.title + " " + Case.description, q), case_match, case_rank)
        .select_from(Case)
        .join(visible, visible.c.id == Case.id),
        hits("note", _headline(Note.body, q), note_match, note_rank)
        .select_from(Note)
        .join(visible, visible.c.id == Note.case_id),
        hits(
            "evidence",
            _headline(Evidence.name + " " + Evidence.description, q),
            evidence_match,
            evidence_rank,
        )
        .select_from(Evidence)
        .join(visible, visible.c.id == Evidence.case_id),
        hits(
            "observable",
            Observable.type + ": " + Observable.value,
            observable_match,
            observable_rank,
        )
        .select_from(Observable)
        .join(CaseObservable, CaseObservable.observable_id == Observable.id)
        .join(visible, visible.c.id == CaseObservable.case_id),
    ]
    combined = union_all(*queries).subquery()
    rows = await db.execute(
        select(combined)
        .order_by(combined.c.rank.desc(), combined.c.case_number.desc())
        .limit(limit)
    )
    return [Hit.model_validate(row, from_attributes=True) for row in rows]
