import os
import re
import hashlib
from lxml import etree
from core.config import get_logger, IMAGE_DIR
from core.utils import clean_text, generate_file_hash
from core.omml_converter import parse_omml_element
from core.docx.extractor import extract_docx_media_and_xml, NAMESPACES
from core.pdf.extractor import extract_pdf_media_and_text

logger = get_logger("physical_dumper")

def parse_paragraph_node_dumper(p_elem, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx):
    if p_elem is None:
        return ""
    parts = []
    for child in p_elem.getchildren():
        tag_local = etree.QName(child.tag).localname
        if tag_local == 'r':
            for r_child in child.getchildren():
                r_child_tag = etree.QName(r_child.tag).localname
                if r_child_tag == 't':
                    if r_child.text:
                        parts.append(r_child.text)
                elif r_child_tag == 'br':
                    parts.append('\n')
                elif r_child_tag == 'tab':
                    parts.append('\t')
            drawings = child.xpath('.//w:drawing', namespaces=NAMESPACES)
            for drawing in drawings:
                blips = drawing.xpath('.//a:blip', namespaces=NAMESPACES)
                if blips:
                    r_id = blips[0].get(f"{{{NAMESPACES['r']}}}embed")
                    if r_id:
                        if r_id in rid_to_placeholder:
                            parts.append(rid_to_placeholder[r_id])
                        elif r_id in zip_images:
                            img_data, ext = zip_images[r_id]
                            img_name = f"IMG_{file_hash}_RAW_I{img_global_idx[0]}{ext}"
                            dest_path = os.path.join(IMAGE_DIR, img_name)
                            with open(dest_path, "wb") as f_img:
                                f_img.write(img_data)
                            placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                            rid_to_placeholder[r_id] = placeholder
                            img_global_idx[0] += 1
                            parts.append(placeholder)
                        else:
                            parts.append(f"[[IMG_MISSING:{r_id}]]")
        elif tag_local in ('oMath', 'oMathPara'):
            math_text = parse_omml_element(child)
            if math_text:
                math_hash = hashlib.md5(math_text.encode('utf-8')).hexdigest()[:12]
                placeholder = f"[[MATH_OMML_{math_hash}]]"
                math_map[placeholder] = math_text
                parts.append(f" {placeholder} ")
        elif tag_local == 'hyperlink':
            for run_child in child.xpath('.//w:r', namespaces=NAMESPACES):
                for r_child in run_child.getchildren():
                    r_child_tag = etree.QName(r_child.tag).localname
                    if r_child_tag == 't':
                        if r_child.text:
                            parts.append(r_child.text)
                    elif r_child_tag == 'br':
                        parts.append('\n')
                    elif r_child_tag == 'tab':
                        parts.append('\t')
    return "".join(parts)

def parse_cell_node_dumper(cell_elem, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx):
    p_texts = []
    for p in cell_elem.xpath('./w:p', namespaces=NAMESPACES):
        p_text = clean_text(parse_paragraph_node_dumper(p, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx))
        if p_text:
            p_texts.append(p_text)
    return "\n".join(p_texts)

def parse_table_element_dumper(table_elem, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx):
    rows = table_elem.xpath('./w:tr', namespaces=NAMESPACES)
    if not rows:
        return ""
    tb_lines = []
    for row in rows:
        cells = row.xpath('./w:tc', namespaces=NAMESPACES)
        cell_texts = [clean_text(parse_cell_node_dumper(c, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx)) for c in cells]
        tb_lines.append("| " + " | ".join(cell_texts) + " |")
    return "\n".join(tb_lines)

def dump_docx_to_raw_markdown(docx_path: str) -> tuple[str, dict]:
    """
    Extracts docx content preserving math (OMML mapped to [[MATH_OMML_hash]] LaTeX placeholders)
    and images mapped to stable placeholders.
    Returns:
        (raw_markdown_text, math_placeholder_map)
    """
    file_hash = generate_file_hash(docx_path)
    relations, zip_images, doc_xml = extract_docx_media_and_xml(docx_path)
    if doc_xml is None:
        return "", {}
        
    doc_tree = etree.fromstring(doc_xml)
    math_map = {}
    rid_to_placeholder = {}
    img_global_idx = [1]
    
    body_elements = doc_tree.xpath('/w:document/w:body/*', namespaces=NAMESPACES)
    text_parts = []
    
    for elem in body_elements:
        tag = elem.tag
        if tag.endswith('p'):
            # Skip if inside table to avoid duplicate processing
            if elem.xpath('./ancestor::w:tbl', namespaces=NAMESPACES):
                continue
            p_text = clean_text(parse_paragraph_node_dumper(elem, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx))
            if p_text:
                text_parts.append(p_text)
        elif tag.endswith('tbl'):
            tb_text = parse_table_element_dumper(elem, math_map, zip_images, file_hash, rid_to_placeholder, img_global_idx)
            if tb_text:
                text_parts.append(tb_text)
                
    return "\n\n".join(text_parts), math_map

def dump_pdf_to_raw_markdown(pdf_path: str) -> tuple[str, dict]:
    """
    Extracts PDF content preserving image placeholders. Math in PDF is already Unicode/plain text.
    Returns:
        (raw_text, {})
    """
    raw_full_text = extract_pdf_media_and_text(pdf_path)
    if not raw_full_text:
        return "", {}
        
    lines_out = []
    for line in raw_full_text.split('\n'):
        line = line.strip()
        if not line or line == "--- PAGE_SEPARATOR ---":
            continue
        lines_out.append(line)
        
    return "\n".join(lines_out), {}
