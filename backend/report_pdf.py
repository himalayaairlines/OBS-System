"""A filed cabin report as a PDF, laid out like the paper form.

Uses reportlab (platypus). The form definition drives the layout, so a change
to a form in report_forms.py changes the PDF too.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from report_forms import form_for

NAVY = colors.HexColor("#0B4F8A")
MUTED = colors.HexColor("#5B6778")
LINE = colors.HexColor("#C6CFDA")
SOFT = colors.HexColor("#F0F3F7")

_base = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=_base["Title"], fontSize=16, leading=20, alignment=0, spaceAfter=2, textColor=NAVY)
SMALL = ParagraphStyle("s", parent=_base["Normal"], fontSize=8.5, leading=11, textColor=MUTED)
BODY = ParagraphStyle("b", parent=_base["Normal"], fontSize=9.5, leading=12.5)
LABEL = ParagraphStyle("l", parent=BODY, textColor=MUTED)
SECTION = ParagraphStyle("h", parent=_base["Heading3"], fontSize=11, leading=14, spaceBefore=8, spaceAfter=4,
                         textColor=NAVY)


def _p(text, style=BODY):
    return Paragraph(escape(str(text if text not in (None, "") else "—")).replace("\n", "<br/>"), style)


def _value(field, value):
    kind = field["type"]
    if kind in ("checks", "checklist"):
        return ", ".join(value or []) or "—"
    if kind == "check":
        return "Yes" if value in (True, "true", "1", "yes", "Yes") else "No"
    return value if value not in (None, "") else "—"


def _grid(rows, widths):
    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def build_pdf(report: dict) -> bytes:
    """`report` is the rendered report (flight_reports._render output)."""
    form = form_for(report["report_type"]) or {"title": report.get("title"), "sections": []}
    payload = report.get("payload") or {}
    buffer = BytesIO()
    width = A4[0] - 30 * mm
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{form.get('title')} {report.get('flight_number', '')}",
                            author="Himalaya Airlines OBS")
    story = [
        _p("Himalaya Airlines | Onboard Sales Revenue System", SMALL),
        Paragraph(escape(form.get("title") or ""), TITLE),
        _p(" | ".join(x for x in (form.get("manual"), form.get("appendix"), form.get("form_code")) if x), SMALL),
        Spacer(1, 6),
        _grid([
            [_p("Flight", LABEL), _p(report.get("flight_number")), _p("Date", LABEL), _p(report.get("flight_date"))],
            [_p("Sector", LABEL), _p(f"{report.get('origin', '')}-{report.get('destination', '')}"),
             _p("Aircraft", LABEL), _p(" ".join(x for x in (report.get("aircraft_type"), report.get("aircraft")) if x))],
            [_p("Crew group", LABEL), _p(report.get("duty_group_label")),
             _p("Filed by", LABEL), _p(f"{report.get('submitted_by') or report.get('created_by') or ''} "
                                       f"{(report.get('submitted_at') or '')[:16].replace('T', ' ')}")],
        ], [26 * mm, width / 2 - 26 * mm, 26 * mm, width / 2 - 26 * mm]),
    ]
    if form.get("statement"):
        story += [Spacer(1, 6), _p(form["statement"], SMALL)]

    for section in form.get("sections", []):
        block = [Paragraph(escape(section["title"]), SECTION)]
        rows = []
        notes = []
        for field in section["fields"]:
            if field["type"] == "note":
                notes.append(_p(field.get("text", ""), SMALL))
                continue
            value = payload.get(field["key"])
            if field["type"] == "rows":
                columns = field.get("columns", [])
                data = [[_p("#", LABEL)] + [_p(c["label"], LABEL) for c in columns]]
                filled = [r for r in (value or []) if any((r or {}).get(c["key"]) for c in columns)]
                for i, row in enumerate(filled or [{}], 1):
                    data.append([_p(i)] + [_p((row or {}).get(c["key"])) for c in columns])
                table = Table(data, colWidths=[10 * mm] + [(width - 10 * mm) / max(1, len(columns))] * len(columns))
                table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), SOFT),
                                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                block.append(table)
                continue
            if field["type"] == "checklist":
                ticked = set(value or [])
                for group in field.get("groups", []):
                    rows.append([_p(group.get("title") or field["label"], LABEL),
                                 _p("\n".join(("[x] " if item in ticked else "[ ] ") + item for item in group["items"]))])
                continue
            rows.append([_p(field.get("label", field["key"]), LABEL), _p(_value(field, value))])
        if rows:
            block.append(_grid(rows, [55 * mm, width - 55 * mm]))
        block += notes
        story.append(KeepTogether(block) if len(rows) < 12 else block[0])
        if len(rows) >= 12:
            story += block[1:]

    story += [Spacer(1, 10), _grid([
        [_p("Form control", LABEL), _p(f"{report.get('issue_label', '')} | {report.get('rev_label', '')} | "
                                       f"Date {report.get('report_date', '')}")],
    ] + ([[_p("Last correction", LABEL),
           _p(f"{report.get('edited_by')}: {report.get('edit_remarks')}")]] if report.get("edit_remarks") else []),
        [55 * mm, width - 55 * mm])]
    revisions = report.get("revisions") or []
    if revisions:
        story.append(Spacer(1, 6))
        story.append(_p("Revision history", SECTION))
        for rev in revisions:
            story.append(_p(f"Rev {int(rev.get('rev_no') or 0):02d} | {str(rev.get('action', '')).lower()} by "
                            f"{rev.get('changed_by')} | {str(rev.get('changed_at') or '')[:16].replace('T', ' ')}"
                            + (f" | {rev['remarks']}" if rev.get("remarks") else ""), SMALL))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 9 * mm, f"{form.get('form_code', '')}  {report.get('issue_label', '')}  "
                                           f"{report.get('rev_label', '')}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
