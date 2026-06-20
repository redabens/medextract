import os
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
from core.docx.extractor import (
    NAMESPACES,
    parse_paragraph_node,
    extract_docx_media_and_xml
)
from core.docx.helpers import parse_table_element, UNNUMBERED_Q_ANNOTATION
from core.post_processor import normalize_question_types, deduplicate_options

logger = get_logger("docx_parser")

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
    
    questions = []
    current_case = None
    current_question = None
    accumulated_context = []
    _unnumbered_q_counter = [0]

    # Regex rules
    case_start_regex = CASE_START_REGEX
    question_start_regex = QUESTION_START_REGEX
    sub_prop_regex = SUB_PROP_REGEX

    # Sequential processing of body block elements (paragraphs and tables)
    body_elements = doc_tree.xpath('/w:document/w:body/*', namespaces=NAMESPACES)
    
    is_interleaved = None
    grid_corrections = []
    
    for elem in body_elements:
        tag = elem.tag
        if tag.endswith('p'):
            raw_text = clean_text(parse_paragraph_node(elem))
            if not raw_text:
                continue
                
            lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
            for text in lines:
                if "fin du cas clinique" in text.lower():
                    current_case = None
                    accumulated_context = []
                    continue
                    
                if case_start_regex.match(text):
                    current_case = {
                        "case_id": hashlib.md5(text.encode('utf-8')).hexdigest()[:12],
                        "case_title": text,
                        "context_text": ""
                    }
                    accumulated_context = []
                    continue
                    
                q_match = question_start_regex.match(text)
                if q_match:
                    q_num = int(q_match.group(1))
                    q_instruction = clean_text(q_match.group(2))

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
                            current_question["_raw_text"] += "\n" + text
                        continue

                    if current_question:
                        questions.append(current_question)

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
                        "correction": None,
                        "_raw_text": text
                    }
                    continue

                is_option_line = bool(parse_options_line(text))
                is_unnumbered_q = (
                    not is_option_line
                    and len(text) > 20
                    and bool(UNNUMBERED_Q_ANNOTATION.search(text))
                    and not sub_prop_regex.match(text)
                    and not (current_question and len(current_question["options"]) == 0 and len(current_question["sub_propositions"]) == 0)
                )
                if is_unnumbered_q:
                    if current_question:
                        questions.append(current_question)

                    _unnumbered_q_counter[0] += 1
                    current_question = {
                        "source_file": filename,
                        "category": category,
                        "case_study": current_case.copy() if current_case else None,
                        "context": "\n".join(accumulated_context) if accumulated_context else (current_case["context_text"] if current_case else None),
                        "question_number": _unnumbered_q_counter[0],
                        "question_type": "SINGLE_CHOICE",
                        "instruction": text,
                        "logic_type": extract_logic_type(text),
                        "has_image": False,
                        "question_images": [],
                        "sub_propositions": [],
                        "options": [],
                        "correction": None,
                        "_raw_text": text
                    }
                    continue
                    
                parsed_opts = parse_options_line(text)
                if not parsed_opts and current_question:
                    next_letter = 'A'
                    if current_question["options"]:
                        last_letter = current_question["options"][-1]["letter"]
                        next_letter = chr(ord(last_letter) + 1)
                    loose_match = re.match(
                        OPTION_LOOSE_PATTERN.format(letter=next_letter), text.strip()
                    ) or re.match(
                        OPTION_LOOSE_PATTERN.format(letter=next_letter.lower()), text.strip(), re.IGNORECASE
                    )
                    if loose_match:
                        parsed_opts = [{
                            "letter": loose_match.group(1).upper(),
                            "text": clean_text(loose_match.group(2)),
                            "is_correct": False
                        }]
                        
                if parsed_opts and current_question:
                    current_question["_raw_text"] += "\n" + text
                    if len(parsed_opts) > 1:
                        if current_question["options"] and parsed_opts[0]["letter"] == 'A':
                            current_question["sub_propositions"] = []
                            for idx_opt, opt in enumerate(current_question["options"]):
                                current_question["sub_propositions"].append({
                                    "id": idx_opt + 1,
                                    "text": opt["text"],
                                    "is_true": None
                                })
                            current_question["options"] = parsed_opts
                            current_question["question_type"] = "K_TYPE"
                        else:
                            current_question["options"].extend(parsed_opts)
                            current_question["question_type"] = "K_TYPE"
                    else:
                        first_opt = parsed_opts[0]
                        if re.match(OPTION_LOOSE_PATTERN.format(letter=first_opt["letter"]), text.strip(), re.IGNORECASE):
                            current_question["options"].extend(parsed_opts)
                    continue
                    
                sub_match = sub_prop_regex.match(text)
                if sub_match and current_question and len(current_question["options"]) == 0:
                    current_question["_raw_text"] += "\n" + text
                    sub_id = int(sub_match.group(1))
                    sub_text = clean_text(sub_match.group(2))
                    current_question["sub_propositions"].append({
                        "id": sub_id,
                        "text": sub_text,
                        "is_true": None
                    })
                    current_question["question_type"] = "K_TYPE"
                    continue
                    
                if current_case:
                    if not current_question:
                        if current_case["context_text"]:
                            current_case["context_text"] += "\n" + text
                        else:
                            current_case["context_text"] = text
                    else:
                        accumulated_context.append(text)
                        current_case["context_text"] += "\n[Mise à jour] " + text
                        current_question["_raw_text"] += "\n" + text
                elif current_question:
                    current_question["_raw_text"] += "\n" + text
                    if "[[IMG_RID:" in text:
                        if current_question["instruction"]:
                            current_question["instruction"] += "\n" + text
                        else:
                            current_question["instruction"] = text

        elif tag.endswith('tbl'):
            table_corrs = parse_table_element(elem, filename)
            if not table_corrs:
                continue

            if is_interleaved is None:
                is_interleaved = (len(questions) == 0)
                logger.info(f"[{filename}] Détection du mode de correction: {'Intercalé' if is_interleaved else 'Grille finale'}")

            if is_interleaved:
                if current_question:
                    if current_question.get("correction") is None:
                        tc = table_corrs[0]
                        if tc.get("is_append"):
                            if len(questions) > 0 and questions[-1].get("correction"):
                                questions[-1]["correction"]["comment"] += "\n" + tc["comment"]
                        else:
                            current_question["correction"] = {
                                "answer_letter": tc["answer_letter"],
                                "comment": tc["comment"],
                                "correction_images": []
                            }
                            correct_letters = re.findall(r'[A-G]', tc["answer_letter"])
                            for opt in current_question["options"]:
                                if opt["letter"] in correct_letters:
                                    opt["is_correct"] = True
                            if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                                current_question["question_type"] = "MULTIPLE_CHOICE"
            else:
                grid_corrections.extend(table_corrs)
                
    if current_question:
        questions.append(current_question)
        
    logger.info(f"[{filename}] Questions détectées: {len(questions)}, Corrections trouvées en grille: {len(grid_corrections)}")
    
    # 4. Shared Normalization & Deduplication
    questions = normalize_question_types(questions)
    questions = deduplicate_options(questions)

    # 5. Grid mode pairing
    if is_interleaved is False and grid_corrections:
        corr_map = {}
        implicit_corrs = []
        for corr in grid_corrections:
            if corr.get("q_num") is not None:
                corr_map[corr["q_num"]] = corr
            else:
                implicit_corrs.append(corr)

        implicit_idx = 0
        for idx, question in enumerate(questions):
            q_num = question.get("question_number")
            
            matching_corr = None
            if q_num in corr_map:
                matching_corr = corr_map[q_num]
            elif implicit_idx < len(implicit_corrs):
                matching_corr = implicit_corrs[implicit_idx]
                implicit_idx += 1

            if matching_corr:
                question["correction"] = {
                    "answer_letter": matching_corr["answer_letter"],
                    "comment": matching_corr["comment"],
                    "correction_images": []
                }
                
                correct_letters = re.findall(r'[A-G]', matching_corr["answer_letter"])
                for opt in question["options"]:
                    if opt["letter"] in correct_letters:
                        opt["is_correct"] = True
                        
                if len(correct_letters) > 1 and question["question_type"] != "K_TYPE":
                    question["question_type"] = "MULTIPLE_CHOICE"

    for question in questions:
        if not question.get("correction"):
            question["correction"] = {
                "answer_letter": "",
                "comment": "Correction non trouvée dans le document.",
                "correction_images": []
            }
            
    # 6. Extract image files from Zip archive on the fly and map placeholders
    for q in questions:
        q_image_idx = 1
        
        # 6.1 Check Case Study Context
        if q["case_study"]:
            matches = re.findall(r'\[\[IMG_RID:(rId\d+)\]\, namespaces=NAMESPACES\b|\[\[IMG_RID:(rId\d+)\]\]', q["case_study"]["context_text"])
            # Flatten matches tuple from re.findall
            matches = [m[0] or m[1] for m in matches if m[0] or m[1]]
            for r_id in matches:
                if r_id in zip_images:
                    img_data, ext = zip_images[r_id]
                    img_name = f"IMG_{file_hash}_Q{q['question_number']}_{q_image_idx}{ext}"
                    dest_path = os.path.join(IMAGE_DIR, img_name)
                    
                    with open(dest_path, "wb") as f_img:
                        f_img.write(img_data)
                        
                    new_placeholder = f"[[{os.path.splitext(img_name)[0]}]]"
                    q["case_study"]["context_text"] = q["case_study"]["context_text"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                    if q["context"]:
                        q["context"] = q["context"].replace(f"[[IMG_RID:{r_id}]]", new_placeholder)
                        
                    q["has_image"] = True
                    q["question_images"].append(img_name)
                    q_image_idx += 1
                    
        # 6.2 Check Question Instruction
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
                
        # 6.3 Check Options
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
                    
        # 6.4 Check Correction
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
