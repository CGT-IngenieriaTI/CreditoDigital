from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


def _template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "autorizacion_centrales_riesgo.pdf"


def build_consent_footer_pdf(*, channel: str, destination_masked: str, transaction_uuid: str) -> bytes:
    template_path = _template_path()
    if not template_path.exists():
        raise ValueError(f"No se encontró la plantilla de autorizaciÃ³n en {template_path}.")

    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    label = "SMS" if str(channel).upper() == "SMS" else "EMAIL"
    footer_text = (
        f"Documento autorizado mediante OTP vía {label} al destino {destination_masked}. "
        f"Id transacción: {transaction_uuid}"
    )

    first_page = writer.pages[0]
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=(float(first_page.mediabox.width), float(first_page.mediabox.height)))
    can.setFont("Helvetica", 8)
    can.setFillGray(0.35)
    can.drawCentredString(float(first_page.mediabox.width) / 2.0, 14, footer_text)
    can.save()
    packet.seek(0)

    overlay_page = PdfReader(packet).pages[0]
    first_page.merge_page(overlay_page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()

