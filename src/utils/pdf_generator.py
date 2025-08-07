import json
import os
import tempfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Frame, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)


def add_background(c, doc):
    """Draw a professional background on each page."""
    c.saveState()
    c.setFillColor(colors.white)
    c.rect(0, 0, A4[0], A4[1], fill=1)
    c.restoreState()

async def pdf_generator(transcriptions, logo_path=None):
    """
    Generates a PDF with aligned transcription columns, wrapping long data entries, and reduced margins.
    
    :param transcriptions: List of transcriptions to include in the PDF.
    :param logo_path: Optional path to a logo image to include in the PDF.
    :return: Path to the generated PDF file.
    """
    # Ensure the logs directory exists
    logs_dir = "./.logs/pdfs"
    os.makedirs(logs_dir, exist_ok=True)

    # Create a temporary file in the logs directory
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=logs_dir) as tmp_file:
        pdf_file_path = tmp_file.name

    # Set up the PDF document with reduced margins
    doc = SimpleDocTemplate(pdf_file_path, pagesize=A4,
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    elements = []

    # Title style with a professional font
    title_style = ParagraphStyle(
        name="Title",
        fontName="Helvetica-Bold",
        fontSize=18,
        alignment=1,  # Center the title
        textColor=colors.darkblue,
    )

    # Content text style
    content_style = ParagraphStyle(
        name="Content",
        fontName="Helvetica",
        fontSize=10,
        leading=12,
    )

    # Title of the document
    title = Paragraph("Conference Transcription", title_style)
    elements.append(title)
    elements.append(Spacer(1, 18))

    # Column headers
    header_data = [['Time', 'User', 'Message']]
    header_table = Table(header_data, colWidths=[1.5*inch, 2*inch, 4*inch])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ]))
    elements.append(header_table)

    # Add the transcriptions
    for log_message in transcriptions:
        if isinstance(log_message, str):
            try:
                log_message = json.loads(log_message)
            except json.JSONDecodeError:
                continue
        
        begin = log_message.get("begin", "N/A")
        display_name = log_message.get("displayName", "N/A")
        data = log_message.get("data", "")

        data_paragraph = Paragraph(data, content_style)
        
        row_data = [[begin, display_name, data_paragraph]]
        row_table = Table(row_data, colWidths=[1.5*inch, 2*inch, 4*inch])
        row_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        elements.append(row_table)

    # Build the PDF
    doc.build(elements, onFirstPage=add_background, onLaterPages=add_background)

    return pdf_file_path
