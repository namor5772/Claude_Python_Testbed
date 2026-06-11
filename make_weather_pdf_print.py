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
OUTPUT_PDF = "/Users/roman/temp/binda_weather_2026-04-10_to_04-12_PRINT.pdf"


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
    # Print-friendly: slightly wider margins, black/white only, bigger fonts.
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Binda NSW Weather Report (Print version)",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        spaceAfter=6,
        textColor=colors.black,
    )

    meta_style = ParagraphStyle(
        "MetaStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.black,
        spaceAfter=10,
    )

    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.black,
    )

    label = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=15,
        textColor=colors.black,
    )

    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11.5,
        leading=15,
        textColor=colors.black,
    )

    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=12,
        textColor=colors.black,
    )

    period = pick_value(lines, "Period")
    condition = pick_value(lines, "- Condition")
    temperature = pick_value(lines, "- Temperature")

    generated = datetime.now().strftime("%a %d %b %Y %H:%M")

    story = []
    story.append(Paragraph("Binda, NSW  3-day Weather Outlook (Print)", title_style))
    story.append(
        Paragraph(
            f"<b>Period:</b> {period or 'Fri 10 Apr 2026 to Sun 12 Apr 2026 (AEST)'}<br/>"
            f"<b>Generated:</b> {generated}",
            meta_style,
        )
    )

    story.append(Paragraph("At-a-glance", h2))
    data = [
        ["Day", "Conditions", "High", "Low", "Notes"],
        ["Fri 10 Apr", "Decreasing cloud; windy", "24C", "8C", "Damaging-winds warning until 7:00 PM"],
        ["Sat 11 Apr", "Clouds & sun; windy, cooler", "16C", "4C", "Big cool change"],
        ["Sun 12 Apr", "Some sun then cloudier; windy", "13C", "2C", "Very cold night / near-frost feel"],
    ]

    tbl = Table(data, colWidths=[24 * mm, 62 * mm, 18 * mm, 18 * mm, 50 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("ALIGN", (2, 1), (3, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 1.25, colors.black),
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(tbl)

    story.append(Spacer(1, 10))
    story.append(Paragraph("Current conditions", h2))
    story.append(
        Paragraph(
            f"<b>Condition:</b> {condition or 'Cloudy'}<br/>"
            f"<b>Temperature:</b> {temperature or '20C (68F)'}",
            body,
        )
    )

    # Warning: print-friendly banner (no orange fill; bold border)
    story.append(Paragraph("Severe Weather Warning (Damaging winds)", h2))
    warning_points = [
        "In effect until <b>7:00 PM AEST</b> Friday 10 April 2026",
        "Main hazard: damaging <b>northwesterly</b> winds",
        "Typical winds <b>607 km/h</b>; peak gusts around <b>100 km/h</b>",
        "Secure loose outdoor items; take care driving on exposed roads",
    ]

    warning_list = ListFlowable(
        [ListItem(Paragraph(p, body), leftIndent=14) for p in warning_points],
        bulletType="bullet",
        leftIndent=10,
        bulletFontName="Helvetica",
        bulletFontSize=11,
    )

    box = Table([[warning_list]], colWidths=[A4[0] - doc.leftMargin - doc.rightMargin])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 1.5, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(box)

    story.append(Paragraph("Day-by-day details", h2))

    day_sections = [
        (
            "Friday 10 April 2026",
            [
                ("Overall", "Decreasing cloud through the day; windy."),
                ("Temperature", "High 24C (76F) / Low 8C (47F)"),
            ],
        ),
        (
            "Saturday 11 April 2026",
            [
                ("Overall", "Windy and cooler with a mix of cloud and sunshine."),
                ("Temperature", "High 16C (60F) / Low 4C (39F)"),
                ("Notes", "A significant cool change compared with Friday, made more noticeable by wind."),
            ],
        ),
        (
            "Sunday 12 April 2026",
            [
                ("Overall", "Some sun, then turning cloudier; windy and cool."),
                ("Temperature", "High 13C (55F) / Low 2C (36F)"),
                ("Notes", "Cold night/early morning conditions are likely; sheltered areas could feel close to near-frost."),
            ],
        ),
    ]

    for day, items in day_sections:
        story.append(Paragraph(day, ParagraphStyle("DayHdr", parent=h2, fontSize=12.5, leading=16)))
        rows = [[Paragraph(k + ":", label), Paragraph(v, body)] for k, v in items]
        dt = Table(rows, colWidths=[32 * mm, A4[0] - doc.leftMargin - doc.rightMargin - 32 * mm])
        dt.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.75, colors.black),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
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
        [ListItem(Paragraph(p, body), leftIndent=14) for p in summary_points],
        bulletType="bullet",
        leftIndent=10,
        bulletFontName="Helvetica",
        bulletFontSize=11,
    )
    story.append(summary_list)

    story.append(Spacer(1, 10))
    story.append(Paragraph("Print version: high-contrast (black/white) with larger type.", small))

    def on_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 9.5)
        canvas.setFillColor(colors.black)
        canvas.drawString(doc_obj.leftMargin, 10 * mm, "Binda, NSW  3-day outlook (Print)")
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
