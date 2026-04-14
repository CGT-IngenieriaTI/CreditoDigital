from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _base_document(buffer: BytesIO) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
    )


def build_legal_document_pdf(title: str, description: str, body_items: list[str]) -> bytes:
    buffer = BytesIO()
    document = _base_document(buffer)
    styles = getSampleStyleSheet()
    title_style = styles["Heading1"]
    title_style.textColor = colors.HexColor("#F47A20")
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        leading=18,
        fontSize=10.5,
        textColor=colors.HexColor("#143A63"),
    )
    story = [
        Paragraph("Congente - Cooperativa de Ahorro y Credito", styles["Title"]),
        Spacer(1, 0.4 * cm),
        Paragraph(title, title_style),
        Spacer(1, 0.3 * cm),
        Paragraph(description, body_style),
        Spacer(1, 0.5 * cm),
    ]
    for item in body_items:
        story.append(Paragraph(f"- {item}", body_style))
        story.append(Spacer(1, 0.2 * cm))
    document.build(story)
    return buffer.getvalue()


def build_decision_pdf(solicitud, decision) -> bytes:
    buffer = BytesIO()
    document = _base_document(buffer)
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#143A63")
    heading = styles["Heading2"]
    heading.textColor = colors.HexColor("#F47A20")
    body = styles["BodyText"]
    body.leading = 16
    body.textColor = colors.HexColor("#243447")

    applicant = solicitud.solicitante
    data = [
        ["Numero de solicitud", solicitud.numero_solicitud],
        ["Resultado", decision.resultado],
        ["Fecha", decision.created_at.strftime("%d/%m/%Y %H:%M")],
        ["Identificacion", applicant.numero_identificacion],
        ["Tipo", applicant.tipo_identificacion],
        ["Primer apellido", applicant.primer_apellido],
        ["Celular", applicant.celular],
        ["Correo", applicant.email],
    ]

    if decision.monto_aprobado:
        data.append(["Monto aprobado", f"${decision.monto_aprobado:,.0f}".replace(",", ".")])
    if decision.plazo_aprobado:
        data.append(["Plazo aprobado", f"{decision.plazo_aprobado} meses"])
    if decision.tasa_interes:
        data.append(["Tasa interes", f"{decision.tasa_interes}% E.A."])

    table = Table(data, colWidths=[5 * cm, 10 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF2FB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D4E1EF")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#243447")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story = [
        Paragraph("Resultado de Solicitud de Credito Digital", styles["Title"]),
        Spacer(1, 0.4 * cm),
        Paragraph("Congente - Consumo", heading),
        Spacer(1, 0.25 * cm),
        Paragraph(decision.mensaje, body),
        Spacer(1, 0.5 * cm),
        table,
        Spacer(1, 0.5 * cm),
        Paragraph(
            "Este documento fue generado automaticamente por el sistema de credito digital.",
            body,
        ),
    ]
    document.build(story)
    return buffer.getvalue()
