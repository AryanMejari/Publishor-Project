import os
import hashlib
from pathlib import Path
import fitz
from docx import Document
from docx2pdf import convert
from docx.shared import RGBColor, Pt
from datetime import datetime
from server import app, send_certificates_email
from firebase_store import get_paper_by_id, update_paper
from firebase_storage import upload_bytes

BASE_DIR = Path(__file__).resolve().parent

def replace_placeholders(doc, replacements):
    """Replace placeholders in all runs across paragraphs and tables with proper formatting."""
    
    # Define font settings
    font_name = "Times New Roman"
    font_size = Pt(16)
    
    def process_paragraph(paragraph):
        # First, combine all runs to ensure we can find split placeholders
        full_text = ''.join(run.text for run in paragraph.runs)
        
        # Check if any placeholder exists in this paragraph
        if not any(placeholder in full_text for placeholder in replacements.keys()):
            return
        
        # Clear all existing runs
        for run in paragraph.runs:
            run.text = ""
        
        # Process the text to find and format replacements
        current_index = 0
        formatted_runs = []
        
        # Find all placeholder positions and their formatting
        placeholder_positions = []
        for placeholder, (value, color) in replacements.items():
            start_idx = full_text.find(placeholder)
            while start_idx != -1:
                end_idx = start_idx + len(placeholder)
                placeholder_positions.append((start_idx, end_idx, value, color, placeholder))
                start_idx = full_text.find(placeholder, end_idx)
        
        # Sort by start index
        placeholder_positions.sort(key=lambda x: x[0])
        
        # Build the formatted text segments
        segments = []
        current_pos = 0
        
        for start, end, value, color, placeholder in placeholder_positions:
            # Add text before the placeholder
            if current_pos < start:
                segments.append((full_text[current_pos:start], None, False))
            
            # Add the replacement value with formatting
            segments.append((str(value), color, placeholder == "{{NAME}}"))
            current_pos = end
        
        # Add any remaining text after the last placeholder
        if current_pos < len(full_text):
            segments.append((full_text[current_pos:], None, False))
        
        # Create runs for each segment
        for text, color, is_bold in segments:
            if text:  # Only create run if there's text
                run = paragraph.add_run(text)
                run.font.name = font_name
                run.font.size = font_size
                if color:
                    run.font.color.rgb = color
                if is_bold:
                    run.bold = True

    # Process all paragraphs
    for paragraph in doc.paragraphs:
        process_paragraph(paragraph)
    
    # Process all tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    process_paragraph(paragraph)

def generate_certificates(paper_id):
    with app.app_context():
        paper = get_paper_by_id(paper_id)
        if not paper:
            print(f"Paper ID {paper_id} not found!")
            return

        authors = [
            paper.get('corresponding_author_name'),
            paper.get('additional_author_name_1'),
            paper.get('additional_author_name_2'),
            paper.get('additional_author_name_3'),
            paper.get('additional_author_name_4'),
            paper.get('additional_author_name_5')
        ]
        authors = [name for name in authors if name]
        certificates_dir = Path(os.getenv("ISIRP_TEMP_DIR", "/tmp")) / "isirp-certificates"
        certificates_dir.mkdir(exist_ok=True)
        certificate_paths = []

        # Format date properly
        if isinstance(paper.get('submission_date'), datetime):
            date_str = paper.get('submission_date').strftime("%d/%m/%y")
        else:
            date_str = str(paper.get('submission_date'))

        certificate_updates = {}

        for i, author_name in enumerate(authors):
            doc = Document(BASE_DIR / "templates" / "certificate_template.docx")

            replacements = {
                "{{NAME}}": (author_name, RGBColor(0, 51, 153)),       # Blue color
                "{{TITLE}}": (paper.get('paper_title'), RGBColor(0, 51, 153)), # Blue color
                "{{ID}}": (paper.get('paper_id'), RGBColor(153, 0, 0)),        # Red color
                "{{DATE}}": (date_str, RGBColor(153, 0, 0))             # Red color
            }

            replace_placeholders(doc, replacements)

            docx_path = certificates_dir / f"{paper.get('paper_id')}_author_{i+1}.docx"
            pdf_path = certificates_dir / f"{paper.get('paper_id')}_author_{i+1}.pdf"
            doc.save(str(docx_path))

            try:
                convert(str(docx_path), str(pdf_path))
                docx_path.unlink()  # remove Word file if PDF created
            except Exception as e:
                # Vercel/Linux does not provide Microsoft Word. Render the
                # pre-converted copy of the same original template instead.
                print(f"docx2pdf failed: {e}. Rendering the original PDF template.")
                docx_path.unlink(missing_ok=True)
                _render_certificate_from_template(
                    pdf_path,
                    author_name,
                    paper.get("paper_title"),
                    paper.get("paper_id"),
                    date_str,
                )

            certificate_paths.append(str(pdf_path))
            certificate_storage_path = upload_bytes(
                pdf_path.read_bytes(),
                f"certificates/{pdf_path.name}",
                "application/pdf",
            )

            # Prepare cert path updates for Firestore
            if i == 0:
                certificate_updates['corresponding_author_certificate'] = certificate_storage_path
            elif i == 1:
                certificate_updates['additional_author_cert_1'] = certificate_storage_path
            elif i == 2:
                certificate_updates['additional_author_cert_2'] = certificate_storage_path
            elif i == 3:
                certificate_updates['additional_author_cert_3'] = certificate_storage_path
            elif i == 4:
                certificate_updates['additional_author_cert_4'] = certificate_storage_path
            elif i == 5:
                certificate_updates['additional_author_cert_5'] = certificate_storage_path

        if certificate_updates:
            certificate_updates["certificate_sha256"] = hashlib.sha256(
                Path(certificate_paths[0]).read_bytes()
            ).hexdigest()
            update_paper(paper.get('paper_id'), certificate_updates)

        send_certificates_email(
            paper.get('corresponding_author_email'),
            paper.get('paper_id'),
            certificate_paths
        )

def _render_certificate_from_template(path, author_name, title, paper_id, date_str):
    template_path = BASE_DIR / "templates" / "certificate_template.pdf"
    if not template_path.is_file():
        raise FileNotFoundError(f"Certificate PDF template not found: {template_path}")

    document = fitz.open(template_path)
    page = document[0]
    page.insert_textbox(
        fitz.Rect(70, 395, 525, 430),
        str(author_name or ""),
        fontname="hebo",
        fontsize=16,
        color=(0, 0.2, 0.6),
        align=fitz.TEXT_ALIGN_CENTER,
    )
    page.insert_textbox(
        fitz.Rect(70, 455, 525, 490),
        str(title or ""),
        fontname="hebo",
        fontsize=13,
        color=(0, 0.2, 0.6),
        align=fitz.TEXT_ALIGN_CENTER,
    )
    page.insert_textbox(
        fitz.Rect(45, 510, 525, 545),
        str(paper_id or ""),
        fontname="helv",
        fontsize=11,
        color=(0.6, 0, 0),
        align=fitz.TEXT_ALIGN_CENTER,
    )
    page.insert_textbox(
        fitz.Rect(210, 540, 390, 575),
        str(date_str or ""),
        fontname="helv",
        fontsize=11,
        color=(0.6, 0, 0),
        align=fitz.TEXT_ALIGN_CENTER,
    )
    document.save(path)
    document.close()


if __name__ == "__main__":
    paper_id = input("Enter Paper ID: ")
    generate_certificates(paper_id)