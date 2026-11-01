"""Case reports (executive summary, technical, post-incident review) as PDF or DOCX.

Layouts are fixed in code. Note Markdown is reproduced as plain source text, so nothing in a
note is interpreted as markup.
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import docx
from fpdf import FPDF, FontFace
from fpdf.enums import XPos, YPos

from app.cases import CaseOut
from app.tenant_config import TenantConfig

FONTS = Path(__file__).parent / "fonts"
PRIVILEGE_NOTICE = "Attorney-Client Privileged / Prepared at Direction of Counsel"
# Characters XML documents cannot hold.
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Kind(StrEnum):
    EXECUTIVE = "executive"
    TECHNICAL = "technical"
    PIR = "pir"


class Format(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


MEDIA_TYPES = {
    Format.PDF: "application/pdf",
    Format.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class Heading:
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class Fields:
    rows: list[tuple[str, str]]


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


Block = Heading | Paragraph | Fields | Table


@dataclass
class Report:
    title: str
    markings: list[str]  # repeated at the top of every page
    blocks: list[Block]


def clean(text: str) -> str:
    return CONTROL.sub("", text)


def when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else ""


def span(start: datetime | None, end: datetime | None) -> str:
    if not (start and end):
        return ""
    hours = (end - start).total_seconds() / 3600
    return f"{hours / 24:.1f} days" if hours >= 48 else f"{hours:.1f} hours"


@dataclass
class Context:
    case: CaseOut
    config: TenantConfig
    tenant: str
    members: dict[UUID, str]
    generated_at: datetime

    def name(self, user_id: UUID | None) -> str:
        return self.members.get(user_id, "Former member") if user_id else "Unassigned"

    def actor(self, actor: str) -> str:
        kind, _, ident = actor.partition(":")
        if kind == "user":
            return self.members.get(UUID(ident), "Former member")
        return "API token" if kind == "token" else "System"

    @property
    def milestones(self) -> dict[str, datetime]:
        return {e.milestone: e.occurred_at for e in self.case.timeline if e.milestone}


def _table(headers: list[str], rows: list[list[str]], empty: str) -> list[Block]:
    return [Table(headers, rows)] if rows else [Paragraph(empty)]


def _overview(ctx: Context) -> list[Block]:
    c = ctx.case
    state = next((s.name for s in ctx.config.states if s.key == c.state), c.state)
    template = ctx.config.template(c.category)
    return [
        Fields(
            [
                ("Case", f"#{c.number} {c.title}"),
                ("Tenant", ctx.tenant),
                ("Type", template.name if template else c.category),
                ("State", state),
                ("Severity", f"{c.priority} {c.severity}"),
                ("Opened", when(c.created_at)),
                ("Closed", when(c.closed_at)),
                ("Resolution", (c.resolution or "").replace("_", " ")),
                ("Owner", ctx.name(c.owner_id)),
                ("Team", c.team or ""),
                ("Regulated data", ", ".join(r.upper() for r in c.regulated_data)),
                ("Data regions", ", ".join(c.data_regions)),
                ("Business units", ", ".join(c.business_units)),
                ("Report generated", when(ctx.generated_at)),
            ]
        )
    ]


def _key_times(ctx: Context) -> list[Block]:
    m = ctx.milestones
    detection = m.get("detection") or ctx.case.detected_at
    return [
        Heading("Key times"),
        Fields(
            [
                ("First compromise", when(m.get("first_compromise"))),
                ("Detection", when(detection)),
                ("Containment", when(m.get("containment") or ctx.case.contained_at)),
                ("Eradication", when(m.get("eradication"))),
                ("Time to detect", span(m.get("first_compromise"), detection)),
                ("Dwell time", span(m.get("first_compromise"), m.get("eradication"))),
            ]
        ),
    ]


def _regulatory(ctx: Context) -> list[Block]:
    c = ctx.case
    decisions = [
        Paragraph(
            f"{'Material' if d.material else 'Not material'}: decided by {d.decided_by} on "
            f"{when(d.decided_at)}. {d.rationale}"
        )
        for d in c.materiality
    ]
    return [
        Heading("Regulatory notifications"),
        *_table(
            ["Obligation", "Due", "Status"],
            [[k.name, when(k.due_at), k.status] for k in c.clocks],
            "No notification clocks.",
        ),
        *decisions,
        *_table(
            ["Sent", "Recipient", "Summary"],
            [
                [
                    when(d.sent_at),
                    f"{d.recipient} ({d.recipient_type.replace('_', ' ')})",
                    d.summary,
                ]
                for d in c.disclosures
            ],
            "No notifications sent.",
        ),
    ]


def executive(ctx: Context) -> list[Block]:
    root_cause = ctx.case.pir.root_cause
    return [
        *_overview(ctx),
        Heading("Summary"),
        Paragraph(ctx.case.description or "No description."),
        *_key_times(ctx),
        *_regulatory(ctx),
        *([Heading("Root cause"), Paragraph(root_cause)] if root_cause else []),
    ]


def technical(ctx: Context) -> list[Block]:
    c = ctx.case
    notes: list[Block] = []
    for note in c.notes:
        marking = " (privileged)" if note.privileged else ""
        notes += [
            Heading(f"Note by {ctx.actor(note.created_by)}, {when(note.created_at)}{marking}"),
            Paragraph(note.body),
        ]
    return [
        *_overview(ctx),
        Heading("Description"),
        Paragraph(c.description or "No description."),
        *_key_times(ctx),
        Heading("Timeline"),
        *_table(
            ["When", "Source", "What happened", "Milestone"],
            [
                [
                    when(e.occurred_at),
                    e.source,
                    e.description,
                    (e.milestone or "").replace("_", " "),
                ]
                for e in c.timeline
            ],
            "No events recorded.",
        ),
        Heading("ATT&CK techniques"),
        Paragraph(", ".join(c.attack_techniques) or "None recorded."),
        Heading("Alerts"),
        *_table(
            ["Received", "Source", "Title"],
            [[when(a.received_at), a.source, a.title] for a in c.alerts],
            "No alerts.",
        ),
        Heading("Observables"),
        *_table(
            ["Type", "Value"], [[o.type, o.defanged] for o in c.observables], "No observables."
        ),
        Heading("Assets"),
        *_table(
            ["Kind", "Name", "CMDB id"],
            [[a.kind.replace("_", " "), a.name, a.external_id or ""] for a in c.assets],
            "No assets.",
        ),
        Heading("Evidence"),
        *_table(
            ["Name", "Kind", "Status", "SHA-256"],
            [[e.name, e.kind, e.status, e.sha256 or ""] for e in c.evidence],
            "No evidence.",
        ),
        Heading("Tasks"),
        *_table(
            ["Task", "Status", "Assignee"],
            [[t.title, t.status, ctx.name(t.assignee_id)] for t in c.tasks],
            "No tasks.",
        ),
        *_regulatory(ctx),
        Heading("Notes"),
        *(notes or [Paragraph("No notes.")]),
    ]


def pir(ctx: Context) -> list[Block]:
    review = ctx.case.pir
    sections = [
        ("Root cause", review.root_cause),
        ("Contributing factors", review.contributing_factors),
        ("What went well", review.went_well),
        ("What did not go well", review.went_poorly),
    ]
    return [
        *_overview(ctx),
        *(
            block
            for title, text in sections
            for block in (Heading(title), Paragraph(text or "Not recorded."))
        ),
        Heading("Action items"),
        *_table(
            ["Action", "Owner", "Due", "Status"],
            [
                [
                    a.title,
                    ctx.name(a.owner_id),
                    when(a.due_at),
                    f"done {when(a.done_at)}" if a.done_at else "open",
                ]
                for a in review.actions
            ],
            "No action items.",
        ),
        *_key_times(ctx),
    ]


BUILDERS: dict[Kind, tuple[str, Callable[[Context], list[Block]]]] = {
    Kind.EXECUTIVE: ("Executive summary", executive),
    Kind.TECHNICAL: ("Technical report", technical),
    Kind.PIR: ("Post-incident review", pir),
}


def build(kind: Kind, ctx: Context) -> Report:
    title, builder = BUILDERS[kind]
    c = ctx.case
    markings = [f"TLP:{c.tlp.upper()}"]
    if c.privileged or any(n.privileged for n in c.notes):
        markings.append(PRIVILEGE_NOTICE)
    return Report(f"{title}: case #{c.number}", markings, builder(ctx))


# --- Rendering ----------------------------------------------------------------------------


class _Pdf(FPDF):
    def __init__(self, markings: list[str]) -> None:
        super().__init__()
        self.markings = markings
        self.add_font("DejaVu", "", FONTS / "DejaVuSans.ttf")
        self.add_font("DejaVu", "B", FONTS / "DejaVuSans-Bold.ttf")
        self.set_auto_page_break(auto=True, margin=15)

    def header(self) -> None:
        self.set_font("DejaVu", "B", 9)
        for marking in self.markings:
            self.cell(0, 5, marking, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DejaVu", "", 8)
        self.cell(0, 5, f"Page {self.page_no()}", align="C")


def to_pdf(report: Report) -> bytes:
    pdf = _Pdf([clean(m) for m in report.markings])
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(0, 8, clean(report.title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for block in report.blocks:
        match block:
            case Heading(text):
                pdf.ln(3)
                pdf.set_font("DejaVu", "B", 12)
                pdf.multi_cell(0, 7, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            case Paragraph(text):
                pdf.set_font("DejaVu", "", 10)
                pdf.multi_cell(0, 5, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            case Fields(rows):
                pdf.set_font("DejaVu", "", 9)
                with pdf.table(
                    col_widths=(1, 3), first_row_as_headings=False, text_align="LEFT"
                ) as table:
                    for label, value in rows:
                        row = table.row()
                        row.cell(clean(label), style=FontFace(emphasis="BOLD"))
                        row.cell(clean(value))
            case Table(headers, rows):
                pdf.set_font("DejaVu", "", 9)
                with pdf.table(
                    headings_style=FontFace(emphasis="BOLD"), text_align="LEFT"
                ) as table:
                    for values in [headers, *rows]:
                        row = table.row()
                        for value in values:
                            row.cell(clean(value))
        pdf.ln(2)
    return bytes(pdf.output())


def to_docx(report: Report) -> bytes:
    document = docx.Document()
    document.sections[0].header.paragraphs[0].text = clean(" | ".join(report.markings))
    document.add_heading(clean(report.title), level=0)
    for block in report.blocks:
        match block:
            case Heading(text):
                document.add_heading(clean(text), level=1)
            case Paragraph(text):
                document.add_paragraph(clean(text))
            case Fields(rows):
                table = document.add_table(rows=0, cols=2, style="Table Grid")
                for label, value in rows:
                    cells = table.add_row().cells
                    cells[0].text, cells[1].text = clean(label), clean(value)
                    cells[0].paragraphs[0].runs[0].bold = True
            case Table(headers, rows):
                table = document.add_table(rows=1, cols=len(headers), style="Table Grid")
                for cell, header in zip(table.rows[0].cells, headers, strict=True):
                    cell.text = clean(header)
                    cell.paragraphs[0].runs[0].bold = True
                for values in rows:
                    for cell, value in zip(table.add_row().cells, values, strict=True):
                        cell.text = clean(value)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


RENDERERS = {Format.PDF: to_pdf, Format.DOCX: to_docx}


def render(report: Report, fmt: Format) -> bytes:
    return RENDERERS[fmt](report)
