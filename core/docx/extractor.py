import os
import zipfile
import re
import hashlib
from lxml import etree
from core.config import get_logger, IMAGE_DIR
from core.utils import clean_text, generate_file_hash
from core.omml_converter import parse_omml_element

logger = get_logger("docx_extractor")

# XML Namespaces used in Word OpenXML documents
NAMESPACES = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'
}

def parse_paragraph_node(p_elem):
    """
    Parses a single paragraph XML element, preserving text runs,
    Office Math equations (OMML), and image references.
    """
    if p_elem is None:
        return ""
        
    parts = []
    
    # Iterate through all direct children of the paragraph to maintain strict reading order
    for child in p_elem.getchildren():
        tag_local = etree.QName(child.tag).localname
        
        # 1. Standard Run
        if tag_local == 'r':
            # Support text, line breaks (br), and tabs in order
            for r_child in child.getchildren():
                r_child_tag = etree.QName(r_child.tag).localname
                if r_child_tag == 't':
                    if r_child.text:
                        parts.append(r_child.text)
                elif r_child_tag == 'br':
                    parts.append('\n')
                elif r_child_tag == 'tab':
                    parts.append('\t')
                    
            # Check for inline drawings (images) in this run
            drawings = child.xpath('.//w:drawing', namespaces=NAMESPACES)
            for drawing in drawings:
                blips = drawing.xpath('.//a:blip', namespaces=NAMESPACES)
                if blips:
                    r_id = blips[0].get(f"{{{NAMESPACES['r']}}}embed")
                    if r_id:
                        parts.append(f" [[IMG_RID:{r_id}]] ")
                        
        # 2. Office Math Equations (OMML)
        elif tag_local in ('oMath', 'oMathPara'):
            math_text = parse_omml_element(child)
            if math_text:
                parts.append(math_text)
                
        # 3. Hyperlinks (which wrap runs)
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

def parse_cell_node(cell_elem):
    """
    Parses a table cell XML element paragraph by paragraph.
    """
    p_texts = []
    for p in cell_elem.xpath('./w:p', namespaces=NAMESPACES):
        p_text = clean_text(parse_paragraph_node(p))
        if p_text:
            p_texts.append(p_text)
    return "\n".join(p_texts)

def extract_docx_media_and_xml(docx_path):
    """
    Opens the docx as a ZIP file, extracts relation mapping (rId -> media path)
    and loads images and the document XML into memory.
    """
    relations = {}
    zip_images = {}
    doc_xml = None
    
    if not os.path.exists(docx_path):
        logger.error(f"Fichier DOCX introuvable: {docx_path}")
        return relations, zip_images, None
        
    with zipfile.ZipFile(docx_path) as z:
        # 1. Parse relationship mapping
        try:
            rels_xml = z.read('word/_rels/document.xml.rels')
            rels_tree = etree.fromstring(rels_xml)
            for rel in rels_tree.xpath('//*[local-name()="Relationship"]'):
                r_id = rel.get('Id')
                target = rel.get('Target')
                r_type = rel.get('Type')
                if r_id and target and "image" in r_type:
                    # Target paths are usually like 'media/image1.png'
                    relations[r_id] = target
        except KeyError:
            logger.warning("Fichier de relations word/_rels/document.xml.rels introuvable.")
            
        # 2. Extract image binaries in memory
        for r_id, target in relations.items():
            zip_path = f"word/{target}"
            try:
                img_data = z.read(zip_path)
                ext = os.path.splitext(target)[1]
                zip_images[r_id] = (img_data, ext)
            except KeyError:
                logger.warning(f"Image relation {r_id} ({zip_path}) introuvable dans l'archive zip.")
                
        # 3. Read main document XML
        doc_xml = z.read('word/document.xml')
        
    return relations, zip_images, doc_xml

def extract_docx_raw_paragraphs(docx_path: str) -> list:
    """
    Extracts a flat list of raw text paragraphs from a DOCX file for use by
    the LLM pipeline (Epic 02). Images are resolved to stable [[IMG_...]]
    placeholders. Correction tables (inside <w:tbl>) are excluded to avoid
    confusion during LLM structuring.
    """
    filename = os.path.basename(docx_path)
    file_hash = generate_file_hash(docx_path)

    relations, zip_images, doc_xml = extract_docx_media_and_xml(docx_path)
    if doc_xml is None:
        return []

    doc_tree = etree.fromstring(doc_xml)

    # Track globally unique image index across all paragraphs
    img_global_idx = [1]
    # Cache rId -> stable placeholder so duplicate references reuse the same name
    rid_to_placeholder = {}

    def resolve_img_rids(text: str, q_num_hint: str = "X") -> str:
        """Replace [[IMG_RID:rIdN]] with stable [[IMG_...]] placeholders."""
        def replacer(m):
            r_id = m.group(1)
            if r_id in rid_to_placeholder:
                return rid_to_placeholder[r_id]
            if r_id in zip_images:
                img_data, ext = zip_images[r_id]
                img_name = f"IMG_{file_hash}_Q{q_num_hint}_I{img_global_idx[0]}{ext}"
                dest_path = os.path.join(IMAGE_DIR, img_name)
                with open(dest_path, "wb") as f_img:
                    f_img.write(img_data)
                placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                rid_to_placeholder[r_id] = placeholder
                img_global_idx[0] += 1
                return placeholder
            return f"[[IMG_MISSING:{r_id}]]"
        return re.sub(r'\[\[IMG_RID:(rId\d+)\]\]', replacer, text)

    paragraphs_out = []
    all_p_elems = doc_tree.xpath('//w:p', namespaces=NAMESPACES)

    for p_elem in all_p_elems:
        # Skip paragraphs that live inside correction tables
        if p_elem.xpath('./ancestor::w:tbl', namespaces=NAMESPACES):
            continue

        raw_text = clean_text(parse_paragraph_node(p_elem))
        if not raw_text:
            continue

        # Split on embedded newlines (e.g. line-break runs)
        for line in raw_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            line = resolve_img_rids(line)
            paragraphs_out.append(line)

    logger.info(f"[{filename}] {len(paragraphs_out)} paragraphes extraits pour le pipeline LLM.")
    return paragraphs_out
