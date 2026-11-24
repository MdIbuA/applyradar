"""The deadline digest: what's overdue, what's due soon, what has no date yet.

build_digest() is plain data (used by GET /digest and the PDF).
render_pdf() turns it into a one-page PDF with ReportLab.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import config, db

HORIZON_DAYS = 14


def local_today() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def build_digest(conn, user_id: int, today: date | None = None) -> dict:
    today = today or local_today()
    due = db.open_with_deadlines(conn, user_id, today + timedelta(days=HORIZON_DAYS))
    for item in due:
        item["days_left"] = (date.fromisoformat(item["deadline"]) - today).days  # code does the arithmetic
    return {
        "today": today.isoformat(),
        "horizon_days": HORIZON_DAYS,
        "overdue": [i for i in due if i["days_left"] < 0],
        "due_soon": [i for i in due if i["days_left"] >= 0],
        "no_deadline": db.open_without_deadline(conn, user_id),
    }


def _when(days_left: int) -> str:
    if days_left < 0:
        return f"{-days_left} day{'s' if days_left != -1 else ''} ago"
    return {0: "TODAY", 1: "tomorrow"}.get(days_left, f"in {days_left} days")


def render_pdf(digest: dict, email: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title="ApplyRadar deadline digest")
    story = [
        Paragraph("ApplyRadar: deadline digest", styles["Title"]),
        Paragraph(f"{escape(email)} &middot; {digest['today']} &middot; next {digest['horizon_days']} days", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]

    def section(heading: str, items: list[dict], with_deadline: bool, tint):
        story.append(Paragraph(f"{heading} ({len(items)})", styles["Heading2"]))
        if not items:
            story.append(Paragraph("Nothing here.", styles["Italic"]))
            story.append(Spacer(1, 3 * mm))
            return
        header = ["Opportunity", "Kind", "Status"] + (["Deadline", "When"] if with_deadline else [])
        rows = [header]
        for i in items:
            # Titles come from users and pasted text: escape them, Paragraph parses markup.
            name = Paragraph(escape(i["title"]) + (f"<br/><font size=8 color='#555555'>{escape(i['organisation'])}</font>"
                                           if i.get("organisation") else ""), styles["BodyText"])
            row = [name, i["kind"], i["status"]]
            if with_deadline:
                row += [i["deadline"], _when(i["days_left"])]
            rows.append(row)
        widths = [80 * mm, 25 * mm, 22 * mm] + ([25 * mm, 22 * mm] if with_deadline else [])
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), tint),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ]))
        story.extend([table, Spacer(1, 5 * mm)])

    section("Overdue", digest["overdue"], True, colors.HexColor("#b91c1c"))
    section("Due soon", digest["due_soon"], True, colors.HexColor("#1d4ed8"))
    section("No deadline yet: find it", digest["no_deadline"], False, colors.HexColor("#4b5563"))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Only items marked interested or applying are listed.", styles["Italic"]))
    doc.build(story)
    return path


def generate_for_user(user_id: int, trigger: str) -> dict:
    """Build the digest, write the PDF, record it. Used by the API and the scheduler."""
    with db.session() as conn:
        user = db.get_user(conn, user_id)
        digest = build_digest(conn, user_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = render_pdf(digest, user["email"], config.REPORTS_DIR / f"user{user_id}" / f"digest-{stamp}.pdf")
        items = len(digest["overdue"]) + len(digest["due_soon"]) + len(digest["no_deadline"])
        return db.record_report(conn, user_id, str(path), trigger, items)
