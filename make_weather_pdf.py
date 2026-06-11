from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    ListFlowable, ListItem
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from datetime import datetime
import os

INPUT_TXT = "/Users/roman/temp/binda_weather_2026-04-10_to_04-12.txt"
OUTPUT_PDF = "/Users/roman/temp/binda_weather_2026-04-10_to_04-12.pdf"


def read_txt(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f]


def pick_value(lines: list[str], prefix: str) -> str | None:
    prefix_low = prefix.lower()
    for ln in lines:
        if ln.strip().lower().startswith(prefix_low):
            return ln.split(":", 1)[1].strip() if ":" in ln else ln.strip()
    return None


def build_pdf(lines: list[str], out_path: str) -> None:
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Binda NSW Weather Report (3 days)",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        spaceAfter=6,
        textColor=colors.HexColor("#0B2545"),
    )

    meta_style = ParagraphStyle(
        "MetaStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#334155"),
        spaceAfter=10,
    )

    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#0F172A"),
    )

    label = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#0F172A"),
    )

    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#111827"),
    )

    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
    )

    # Pull meta info from the txt if present
    period = pick_value(lines, "Period")
    condition = pick_value(lines, "- Condition")
    temperature = pick_value(lines, "- Temperature")

    generated = datetime.now().strftime("%a %d %b %Y %H:%M")

    story = []
    story.append(Paragraph("Binda, NSW — 3-day Weather Outlook", title_style))
    story.append(
        Paragraph(
            f"<b>Period:</b> {period or 'Fri 10 Apr 2026 to Sun 12 Apr 2026 (AEST)'}<br/>"
            f"<b>Generated:</b> {generated}",
            meta_style,
        )
    )

    # At-a-glance table (based on the report we generated)
    story.append(Paragraph("At-a-glance", h2))
    data = [
        ["Day", "Conditions", "High", "Low", "Notes"],
        ["Fri 10 Apr", "Decreasing cloud; windy", "24°C", "8°C", "Damaging-winds warning until 7:00 PM"],
        ["Sat 11 Apr", "Clouds & sun; windy, cooler", "16°C", "4°C", "Big cool change"],
        ["Sun 12 Apr", "Some sun then cloudier; windy", "13°C", "2°C", "Very cold night / near-frost feel"],
    ]

    tbl = Table(data, colWidths=[22 * mm, 60 * mm, 18 * mm, 18 * mm, 52 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B2545")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("ALIGN", (2, 1), (3, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F8FAFC"), colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(tbl)

    # Current conditions
    story.append(Spacer(1, 8))
    story.append(Paragraph("Current conditions", h2))
    story.append(
        Paragraph(
            f"<b>Condition:</b> {condition or 'Cloudy'}<br/>"
            f"<b>Temperature:</b> {temperature or '20°C (68°F)'}",
            body,
        )
    )

    # Warning callout
    story.append(Paragraph("Severe Weather Warning (Damaging winds)", h2))
    warning_points = [
        "In effect until <b>7:00 PM AEST</b> Friday 10 April 2026",
        "Main hazard: damaging <b>northwesterly</b> winds",
        "Typical winds <b>60–70 km/h</b>; peak gusts around <b>100 km/h</b>",
        "Secure loose outdoor items; take care driving on exposed roads",
    ]

    warning_list = ListFlowable(
        [ListItem(Paragraph(p, body), leftIndent=12) for p in warning_points],
        bulletType="bullet",
        leftIndent=10,
        bulletFontName="Helvetica",
        bulletFontSize=10,
    )

    box = Table([[warning_list]], colWidths=[A4[0] - doc.leftMargin - doc.rightMargin])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7ED")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#FB923C")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(box)

    # Day-by-day details
    story.append(Paragraph("Day-by-day details", h2))

    day_sections = [
        (
            "Friday 10 April 2026",
            [
                ("Overall", "Decreasing cloud through the day; windy."),
                ("Temperature", "High 24°C (76°F) / Low 8°C (47°F)"),
            ],
        ),
        (
            "Saturday 11 April 2026",
            [
                ("Overall", "Windy and cooler with a mix of cloud and sunshine."),
                ("Temperature", "High 16°C (60°F) / Low 4°C (39°F)"),
                ("Notes", "A significant cool change compared with Friday, made more noticeable by wind."),
            ],
        ),
        (
            "Sunday 12 April 2026",
            [
                ("Overall", "Some sun, then turning cloudier; windy and cool."),
                ("Temperature", "High 13°C (55°F) / Low 2°C (36°F)"),
                ("Notes", "Cold night/early morning conditions are likely; sheltered areas could feel close to near-frost."),
            ],
        ),
    ]

    for day, items in day_sections:
        story.append(Paragraph(day, ParagraphStyle("DayHdr", parent=h2, fontSize=11.5)))
        rows = [[Paragraph(k + ":", label), Paragraph(v, body)] for k, v in items]
        dt = Table(rows, colWidths=[28 * mm, A4[0] - doc.leftMargin - doc.rightMargin - 28 * mm])
        dt.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(dt)

    story.append(Spacer(1, 6))
    story.append(Paragraph("Quick planning summary", h2))
    summary_points = [
        "Warmest daytime: <b>Friday</b> (but also the windiest).",
        "Coldest nights: <b>Saturday night</b> and <b>Sunday night</b>.",
        "Clothing: layers + a windproof outer layer; warm jacket for evenings/early mornings.",
    ]
    summary_list = ListFlowable(
        [ListItem(Paragraph(p, body), leftIndent=12) for p in summary_points],
        bulletType="bullet",
        leftIndent=10,
        bulletFontName="Helvetica",
        bulletFontSize=10,
    )
    story.append(summary_list)

    story.append(Spacer(1, 10))
    story.append(Paragraph("Formatted PDF generated from the original text report.", small))

    def on_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(doc_obj.leftMargin, 10 * mm, "Binda, NSW — 3-day outlook")
        canvas.drawRightString(A4[0] - doc_obj.rightMargin, 10 * mm, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


if __name__ == "__main__":
    if not os.path.exists(INPUT_TXT):
        raise SystemExit(f"Input file not found: {INPUT_TXT}")

    lines = read_txt(INPUT_TXT)
    os.makedirs(os.path.dirname(OUTPUT_PDF), exist_ok=True)
    build_pdf(lines, OUTPUT_PDF)
    print(OUTPUT_PDF)
