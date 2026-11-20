"""
Generates formatted Word (.docx) documents from Markdown files in policies/ and docs/.
"""
import re
from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def set_cell_background(cell, fill_hex: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill_hex)
    tcPr.append(shd)


def add_formatted_runs(paragraph, text: str, base_color=None):
    # Split by **bold** or `code`
    tokens = re.split(r'(\*\*.*?\*\*|`.*?`)', text)
    for token in tokens:
        if not token:
            continue
        if token.startswith('**') and token.endswith('**'):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        elif token.startswith('`') and token.endswith('`'):
            run = paragraph.add_run(token[1:-1])
            run.font.name = 'Consolas'
            run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor(0x0F, 0x4C, 0x81)
        else:
            run = paragraph.add_run(token)
        if base_color and not (token.startswith('`') and token.endswith('`')):
            run.font.color.rgb = base_color


def md_to_docx(md_path: Path, docx_path: Path):
    doc = Document()
    # Ensure w:zoom in settings.xml has w:percent="100" for strict OOXML schema compliance
    settings_el = doc.settings.element
    zoom_el = settings_el.find(qn('w:zoom'))
    if zoom_el is not None and zoom_el.get(qn('w:percent')) is None:
        zoom_el.set(qn('w:percent'), '100')

    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.85)
        section.right_margin = Inches(0.85)

    style = doc.styles['Normal']
    style.font.name = 'Segoe UI'
    style.font.size = Pt(10.5)
    style.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)

    lines = md_path.read_text(encoding='utf-8').splitlines()
    i = 0
    in_code_block = False
    code_lines = []

    while i < len(lines):
        line = lines[i]

        # Handle code blocks
        if line.strip().startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_lines = []
            else:
                in_code_block = False
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.25)
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(6)
                run = p.add_run('\n'.join(code_lines))
                run.font.name = 'Consolas'
                run.font.size = Pt(8.5)
                run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        stripped = line.strip()
        if not stripped or stripped == '---':
            i += 1
            continue

        # Handle Markdown Tables
        if stripped.startswith('|') and stripped.endswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|') and lines[i].strip().endswith('|'):
                row_str = lines[i].strip()
                # skip separator row | :--- |
                if not re.match(r'^\|\s*[:\-]+', row_str):
                    cells = [c.strip() for c in row_str.strip('|').split('|')]
                    table_lines.append(cells)
                i += 1

            if table_lines:
                num_cols = max(len(r) for r in table_lines)
                table = doc.add_table(rows=len(table_lines), cols=num_cols)
                table.style = 'Table Grid'
                for r_idx, row_data in enumerate(table_lines):
                    for c_idx in range(num_cols):
                        cell_text = row_data[c_idx] if c_idx < len(row_data) else ''
                        cell = table.cell(r_idx, c_idx)
                        p = cell.paragraphs[0]
                        p.paragraph_format.space_before = Pt(2)
                        p.paragraph_format.space_after = Pt(2)
                        if r_idx == 0:
                            set_cell_background(cell, '0F4C81')
                            add_formatted_runs(p, cell_text, base_color=RGBColor(0xFF, 0xFF, 0xFF))
                            for run in p.runs:
                                run.bold = True
                                run.font.size = Pt(9.5)
                        else:
                            if r_idx % 2 == 1:
                                set_cell_background(cell, 'F8FAFC')
                            add_formatted_runs(p, cell_text)
                            for run in p.runs:
                                run.font.size = Pt(9.5)
                doc.add_paragraph()
            continue

        # Headings
        if stripped.startswith('# '):
            h = doc.add_heading(stripped[2:].strip(), level=1)
            for run in h.runs:
                run.font.name = 'Segoe UI Semibold'
                run.font.size = Pt(18)
                run.font.color.rgb = RGBColor(0x0F, 0x4C, 0x81)
        elif stripped.startswith('## '):
            h = doc.add_heading(stripped[3:].strip(), level=2)
            for run in h.runs:
                run.font.name = 'Segoe UI Semibold'
                run.font.size = Pt(13.5)
                run.font.color.rgb = RGBColor(0x0F, 0x4C, 0x81)
        elif stripped.startswith('### '):
            h = doc.add_heading(stripped[4:].strip(), level=3)
            for run in h.runs:
                run.font.name = 'Segoe UI Semibold'
                run.font.size = Pt(11.5)
                run.font.color.rgb = RGBColor(0x1E, 0x3A, 0x8A)
        elif stripped.startswith('> '):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.3)
            add_formatted_runs(p, stripped[2:].strip(), base_color=RGBColor(0x0F, 0x4C, 0x81))
        elif re.match(r'^[-*]\s+', stripped):
            p = doc.add_paragraph(style='List Bullet')
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(2)
            add_formatted_runs(p, re.sub(r'^[-*]\s+', '', stripped))
        elif re.match(r'^\d+\.\s+', stripped):
            p = doc.add_paragraph(style='List Number')
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(2)
            add_formatted_runs(p, re.sub(r'^\d+\.\s+', '', stripped))
        else:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(4)
            add_formatted_runs(p, stripped)

        i += 1

    doc.save(str(docx_path))


def main():
    root = Path(__file__).resolve().parent.parent
    policies_dir = root / 'policies'
    docs_dir = root / 'docs'

    count = 0
    for md_file in sorted(policies_dir.glob('*.md')):
        out_docx = policies_dir / f"{md_file.stem}.docx"
        md_to_docx(md_file, out_docx)
        print(f"Generated policy DOCX: {out_docx.name}")
        count += 1

    for md_file in sorted(docs_dir.glob('*.md')):
        out_docx = docs_dir / f"{md_file.stem}.docx"
        md_to_docx(md_file, out_docx)
        print(f"Generated deliverable DOCX: {out_docx.name}")
        count += 1

    print(f"Successfully generated {count} Word (.docx) documents.")


if __name__ == '__main__':
    main()
