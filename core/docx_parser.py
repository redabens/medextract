import os
import zipfile
import re
import hashlib
from lxml import etree
from core.config import (
    get_logger,
    IMAGE_DIR,
    CASE_START_REGEX,
    QUESTION_START_REGEX,
    SUB_PROP_REGEX,
    OPTION_LOOSE_PATTERN
)
from core.utils import (
    clean_text,
    generate_file_hash,
    extract_logic_type,
    parse_options_line
)
from core.omml_converter import parse_omml_element

logger = get_logger("docx_parser")

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
    for p in cell_elem.xpath('.//w:p', namespaces=NAMESPACES):
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

def parse_docx_to_qcm(docx_path, category):
    """
    Main parser method that processes a DOCX file to extract questions,
    options, sub-propositions, clinical cases, and matching correction tables.
    """
    filename = os.path.basename(docx_path)
    file_hash = generate_file_hash(docx_path)
    
    # 1. Load XML and extract original images
    relations, zip_images, doc_xml = extract_docx_media_and_xml(docx_path)
    if doc_xml is None:
        return []
        
    doc_tree = etree.fromstring(doc_xml)
    
    # 2. Parse corrections tables
    # Structure target list: [{"answer_letter": str, "comment": str, "images": list}]
    corrections = []
    
    for table_elem in doc_tree.xpath('//w:tbl', namespaces=NAMESPACES):
        rows = table_elem.xpath('.//w:tr', namespaces=NAMESPACES)
        if not rows:
            continue
            
        # Process the rows of the table
        for r_idx, row in enumerate(rows):
            cells = row.xpath('.//w:tc', namespaces=NAMESPACES)
            if not cells:
                continue
                
            cell_texts = [parse_cell_node(c) for c in cells]
            
            # Simple check if this row contains correction data
            # Format 2 columns: [Answers, Comment]
            if len(cell_texts) == 2:
                ans = cell_texts[0].strip()
                # Skip header row if exists
                if ans.lower() in ("question", "numéro", "num", "réponse", "réponses", "correction"):
                    continue
                corrections.append({
                    "answer_letter": ans,
                    "comment": cell_texts[1],
                    "r_idx": r_idx
                })
            # Format 3 columns: [Question ID/Num, Answers, Comment]
            elif len(cell_texts) == 3:
                ans = cell_texts[1].strip()
                # Skip header row
                if ans.lower() in ("réponse", "réponses", "correction") or cell_texts[0].lower() in ("question", "numéro", "num"):
                    continue
                corrections.append({
                    "answer_letter": ans,
                    "comment": cell_texts[2],
                    "r_idx": r_idx
                })
                
    # 3. Parse paragraphs sequentially for questions and clinical cases
    questions = []
    current_case = None
    current_question = None
    accumulated_context = []
    
    # Regex rules
    case_start_regex = CASE_START_REGEX
    question_start_regex = QUESTION_START_REGEX
    sub_prop_regex = SUB_PROP_REGEX
    
    paragraphs = doc_tree.xpath('//w:p', namespaces=NAMESPACES)
    
    for p_elem in paragraphs:
        # Skip correction tables text as they are parsed separately
        # (Inside w:tbl elements. Since xpath w:p gets all paragraphs including those in tables,
        # we check if this paragraph is inside a table)
        if p_elem.xpath('./ancestor::w:tbl', namespaces=NAMESPACES):
            continue
            
        raw_text = clean_text(parse_paragraph_node(p_elem))
        if not raw_text:
            continue
            
        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
        for text in lines:
            # Detect end of clinical case
            if "fin du cas clinique" in text.lower():
                current_case = None
                accumulated_context = []
                continue
                
            # Detect start of clinical case
            if case_start_regex.match(text):
                current_case = {
                    "case_id": hashlib.md5(text.encode('utf-8')).hexdigest()[:12],
                    "case_title": text,
                    "context_text": ""
                }
                accumulated_context = []
                continue
                
            # Detect start of question
            q_match = question_start_regex.match(text)
            if q_match:
                q_num = int(q_match.group(1))
                q_instruction = clean_text(q_match.group(2))
                
                # Heuristic to detect K-Type numbered sub-propositions (1-5) falsely matched as question start
                is_sub_prop = False
                if current_question and len(current_question["options"]) == 0 and 1 <= q_num <= 5:
                    is_sub_prop = True
                    
                if is_sub_prop:
                    if len(current_question["options"]) == 0:
                        current_question["sub_propositions"].append({
                            "id": q_num,
                            "text": q_instruction,
                            "is_true": None
                        })
                        current_question["question_type"] = "K_TYPE"
                    continue
                    
                # Save preceding question
                if current_question:
                    questions.append(current_question)
                    
                # Initialize new question
                current_question = {
                    "source_file": filename,
                    "category": category,
                    "case_study": current_case.copy() if current_case else None,
                    "context": "\n".join(accumulated_context) if accumulated_context else (current_case["context_text"] if current_case else None),
                    "question_number": q_num,
                    "question_type": "SINGLE_CHOICE",
                    "instruction": q_instruction,
                    "logic_type": extract_logic_type(text),
                    "has_image": False,
                    "question_images": [],
                    "sub_propositions": [],
                    "options": [],
                    "correction": None
                }
                continue
                
            # Detect option (A, B, C...)
            parsed_opts = parse_options_line(text)
            if not parsed_opts and current_question:
                next_letter = 'A'
                if current_question["options"]:
                    last_letter = current_question["options"][-1]["letter"]
                    next_letter = chr(ord(last_letter) + 1)
                loose_match = re.match(OPTION_LOOSE_PATTERN.format(letter=next_letter), text.strip())
                if loose_match:
                    parsed_opts = [{
                        "letter": loose_match.group(1).upper(),
                        "text": clean_text(loose_match.group(2)),
                        "is_correct": False
                    }]
                    
            if parsed_opts and current_question:
                if len(parsed_opts) > 1:
                    # Check for K-Type shift: we already have options, and we receive a new set of multiple options starting with A
                    if current_question["options"] and parsed_opts[0]["letter"] == 'A':
                        # Shift existing options to sub_propositions
                        current_question["sub_propositions"] = []
                        for idx, opt in enumerate(current_question["options"]):
                            current_question["sub_propositions"].append({
                                "id": idx + 1,
                                "text": opt["text"],
                                "is_true": None
                            })
                        # Overwrite options with the new ones
                        current_question["options"] = parsed_opts
                        current_question["question_type"] = "K_TYPE"
                    else:
                        current_question["options"].extend(parsed_opts)
                        current_question["question_type"] = "K_TYPE"
                else:
                    first_opt = parsed_opts[0]
                    if re.match(OPTION_LOOSE_PATTERN.format(letter=first_opt["letter"]), text.strip()):
                        current_question["options"].extend(parsed_opts)
                continue
                
            # Detect K-type sub-proposition (1, 2, 3...)
            sub_match = sub_prop_regex.match(text)
            if sub_match and current_question and len(current_question["options"]) == 0:
                sub_id = int(sub_match.group(1))
                sub_text = clean_text(sub_match.group(2))
                current_question["sub_propositions"].append({
                    "id": sub_id,
                    "text": sub_text,
                    "is_true": None
                })
                current_question["question_type"] = "K_TYPE"
                continue
                
            # Handle Clinical Case Text Updates/Context
            if current_case:
                if not current_question:
                    if current_case["context_text"]:
                        current_case["context_text"] += "\n" + text
                    else:
                        current_case["context_text"] = text
                else:
                    accumulated_context.append(text)
                    current_case["context_text"] += "\n[Mise à jour] " + text
                
    # Save the last question
    if current_question:
        questions.append(current_question)
        
    logger.info(f"[{filename}] Questions détectées: {len(questions)}, Corrections trouvées: {len(corrections)}")
    
    # 4. Pair 1-to-1 questions with corrections
    for idx, question in enumerate(questions):
        if idx < len(corrections):
            corr = corrections[idx]
            question["correction"] = {
                "answer_letter": corr["answer_letter"],
                "comment": corr["comment"],
                "correction_images": []
            }
            
            # Map correction answer letters to final option bools
            correct_letters = re.findall(r'[A-E]', corr["answer_letter"])
            for opt in question["options"]:
                if opt["letter"] in correct_letters:
                    opt["is_correct"] = True
                    
            # Auto-deduce type if multiple letters in standard choice
            if len(correct_letters) > 1 and question["question_type"] != "K_TYPE":
                question["question_type"] = "MULTIPLE_CHOICE"
        else:
            # Default empty correction
            question["correction"] = {
                "answer_letter": "",
                "comment": "Correction non trouvée dans le document.",
                "correction_images": []
            }
            
    # 5. Resolve image placeholders and extract image files
    for q in questions:
        # Search all fields for image references
        img_fields = ["instruction", "context"]
        if q["case_study"]:
            img_fields.append("case_study.context_text")
            
        # A list to keep track of images processed in this question
        q_image_idx = 1
        
        # 5.1 Check Case Study Context
        if q["case_study"]:
            matches = re.findall(r'\[\[IMG_RID:(rId\d+)\]\]', q["case_study"]["context_text"])
            for r_id in matches:
                if r_id in zip_images:
                    img_data, ext = zip_images[r_id]
                    img_name = f"IMG_{file_hash}_Q{q['question_number']}_{q_image_idx}{ext}"
                    dest_path = os.path.join(IMAGE_DIR, img_name)
                    
                    with open(dest_path, "wb") as f_img:
                        f_img.write(img_data)
                        
                    # Update text placeholders
                    new_placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                    q["case_study"]["context_text"] = q["case_study"]["context_text"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                    if q["context"]:
                        q["context"] = q["context"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                        
                    q["has_image"] = True
                    q["question_images"].append(img_name)
                    q_image_idx += 1
                    
        # 5.2 Check Question Instruction
        matches = re.findall(r'\[\[IMG_RID:(rId\d+)\]\]', q["instruction"])
        for r_id in matches:
            if r_id in zip_images:
                img_data, ext = zip_images[r_id]
                img_name = f"IMG_{file_hash}_Q{q['question_number']}_{q_image_idx}{ext}"
                dest_path = os.path.join(IMAGE_DIR, img_name)
                
                with open(dest_path, "wb") as f_img:
                    f_img.write(img_data)
                    
                new_placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                q["instruction"] = q["instruction"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                q["has_image"] = True
                q["question_images"].append(img_name)
                q_image_idx += 1
                
        # 5.3 Check Options
        for opt in q["options"]:
            matches = re.findall(r'\[\[IMG_RID:(rId\d+)\]\]', opt["text"])
            for r_id in matches:
                if r_id in zip_images:
                    img_data, ext = zip_images[r_id]
                    img_name = f"IMG_{file_hash}_Q{q['question_number']}_{q_image_idx}{ext}"
                    dest_path = os.path.join(IMAGE_DIR, img_name)
                    
                    with open(dest_path, "wb") as f_img:
                        f_img.write(img_data)
                        
                    new_placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                    opt["text"] = opt["text"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                    q["has_image"] = True
                    q["question_images"].append(img_name)
                    q_image_idx += 1
                    
        # 5.4 Check Correction
        corr_img_idx = 1
        if q["correction"] and q["correction"]["comment"]:
            matches = re.findall(r'\[\[IMG_RID:(rId\d+)\]\]', q["correction"]["comment"])
            for r_id in matches:
                if r_id in zip_images:
                    img_data, ext = zip_images[r_id]
                    img_name = f"IMG_{file_hash}_Q{q['question_number']}_CORR_{corr_img_idx}{ext}"
                    dest_path = os.path.join(IMAGE_DIR, img_name)
                    
                    with open(dest_path, "wb") as f_img:
                        f_img.write(img_data)
                        
                    new_placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                    q["correction"]["comment"] = q["correction"]["comment"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                    q["correction"]["correction_images"].append(img_name)
                    corr_img_idx += 1
                    
    return questions


def extract_docx_raw_paragraphs(docx_path: str) -> list:
    """
    Extracts a flat list of raw text paragraphs from a DOCX file for use by
    the LLM pipeline (Epic 02). Images are resolved to stable [[IMG_...]]
    placeholders. Correction tables (inside <w:tbl>) are excluded to avoid
    confusion during LLM structuring.

    Args:
        docx_path: Absolute path to the .docx file.

    Returns:
        List of non-empty cleaned paragraph strings.
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
    rid_to_placeholder: dict = {}

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
