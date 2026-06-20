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
    parse_cell_node,
    parse_paragraph_node,
    extract_docx_media_and_xml
)

logger = get_logger("docx_parser")

def parse_table_element(table_elem, filename):
    """
    Parses a single correction table element and returns list of correction dictionaries.
    """
    # Skip nested tables to avoid double-processing or column count pollution
    if table_elem.xpath('./ancestor::w:tc', namespaces=NAMESPACES):
        return []

    rows = table_elem.xpath('./w:tr', namespaces=NAMESPACES)
    if not rows:
        return []

    # Detect number of columns in first row
    first_row_cells = rows[0].xpath('./w:tc', namespaces=NAMESPACES)
    n_cols = len(first_row_cells)

    # Skip large pedagogical tables (> 3 columns) — not correction tables
    if n_cols > 3:
        logger.debug(f"[{filename}] Table ignorée (tableau pédagogique, {n_cols} colonnes).")
        return []

    table_corrs = []

    # New format: multi-line table (each row = one line of the correction comment)
    # Heuristic: if table has 1 column and multiple rows, it belongs to the PREVIOUS correction
    if n_cols == 1 and len(rows) > 1:
        extra_lines = []
        for row in rows:
            cells = row.xpath('./w:tc', namespaces=NAMESPACES)
            if cells:
                cell_text = parse_cell_node(cells[0]).strip()
                if cell_text:
                    extra_lines.append(cell_text)
        if extra_lines:
            first_line = extra_lines[0].strip()
            ans_match = re.match(r'^([A-G]{1,7})', first_line)
            if ans_match:
                ans = ans_match.group(1).upper()
                comment = "\n".join(extra_lines[1:]) if len(extra_lines) > 1 else ""
                table_corrs.append({"answer_letter": ans, "comment": comment, "q_num": None})
            else:
                comment = "\n".join(extra_lines)
                table_corrs.append({"answer_letter": "", "comment": comment, "q_num": None, "is_append": True})
        return table_corrs

    # Standard format: process row by row
    for r_idx, row in enumerate(rows):
        cells = row.xpath('./w:tc', namespaces=NAMESPACES)
        if not cells:
            continue

        cell_texts = [parse_cell_node(c) for c in cells]

        # Format 2 columns: [QuestionNum-Answer, Comment] or [Answer, Comment]
        if len(cell_texts) == 2:
            ans = cell_texts[0].strip()
            # Skip header row if exists
            if ans.lower() in ("question", "numéro", "num", "réponse", "réponses", "correction"):
                continue

            # Try to match both question number and answer, e.g. "31-B" or "61) D"
            match = re.match(r'^(?:Q|QST|Question\s*)?(\d+)\s*[\s\.:\)-]+\s*([A-G]{1,7})$', ans, re.IGNORECASE)
            if match:
                q_num = int(match.group(1))
                ans_letter = match.group(2).upper()
                table_corrs.append({
                    "q_num": q_num,
                    "answer_letter": ans_letter,
                    "comment": cell_texts[1]
                })
            else:
                # Fallback to answer only, check if valid letter sequence up to G
                ans_clean = re.sub(r'[\s,+]', '', ans)
                if re.match(r'^[A-Ga-g]{1,7}$', ans_clean):
                    table_corrs.append({
                        "q_num": None,
                        "answer_letter": ans.upper(),
                        "comment": cell_texts[1]
                    })
        # Format 3 columns: [Question ID/Num, Answers, Comment]
        elif len(cell_texts) == 3:
            q_col = cell_texts[0].strip()
            ans = cell_texts[1].strip()
            # Skip header rows
            if ans.lower() in ("réponse", "réponses", "correction") or q_col.lower() in ("question", "numéro", "num"):
                continue

            # Extract question number from Column 0 (e.g. "121-")
            q_num_match = re.search(r'(\d+)', q_col)
            q_num = int(q_num_match.group(1)) if q_num_match else None

            # Validate that ans is a valid answer letter sequence up to G
            ans_clean = re.sub(r'[\s,+/&\-]', '', ans)
            if re.match(r'^[A-Ga-g]{0,7}$', ans_clean):
                table_corrs.append({
                    "q_num": q_num,
                    "answer_letter": ans.upper(),
                    "comment": cell_texts[2]
                })
    return table_corrs

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
    # Auto-counter for unnumbered questions (new format)
    _unnumbered_q_counter = [0]

    # Regex rules
    case_start_regex = CASE_START_REGEX
    question_start_regex = QUESTION_START_REGEX
    sub_prop_regex = SUB_PROP_REGEX

    # Detect questions without leading number:
    # Pattern: sentence ending with explicit exam annotation like (2023 P8-1T) or containing (cochez...)
    UNNUMBERED_Q_ANNOTATION = re.compile(
        r'\(?\d{4}\s+P\d+-\d+T\)?\s*$|'           # Exam source annotation: (2023 P8-1T)
        r'\(cochez\s+la\s+r[eé]ponse|'             # Explicit "(cochez la réponse..."
        r'cocher\s+la\s+r[eé]ponse|'               # Without parenthesis "cocher la réponse"
        r'\(indiquez|'                              # "(indiquez..."
        r'\(parmi\s+les',                           # "(parmi les..."
        re.IGNORECASE
    )

    # Sequential processing of body block elements (interleaved paragraphs and tables)
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
                    
                # Detect start of question (numbered format: "42. texte")
                q_match = question_start_regex.match(text)
                if q_match:
                    q_num = int(q_match.group(1))
                    q_instruction = clean_text(q_match.group(2))

                    # Heuristic: K-Type numbered sub-propositions (1-5) falsely matched as question start
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
                        "correction": None,
                        "_raw_text": text
                    }
                    continue

                # Detect unnumbered questions
                is_option_line = bool(parse_options_line(text))
                is_unnumbered_q = (
                    not is_option_line
                    and len(text) > 20
                    and bool(UNNUMBERED_Q_ANNOTATION.search(text))
                    and not sub_prop_regex.match(text)
                    and not (current_question and len(current_question["options"]) == 0 and len(current_question["sub_propositions"]) == 0)
                )
                if is_unnumbered_q:
                    # Save preceding question
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
                    
                # Detect option (A, B, C...)
                parsed_opts = parse_options_line(text)
                if not parsed_opts and current_question:
                    next_letter = 'A'
                    if current_question["options"]:
                        last_letter = current_question["options"][-1]["letter"]
                        next_letter = chr(ord(last_letter) + 1)
                    # Also try lowercase version of the next expected letter
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
                        # Check for K-Type shift: we already have options, and we receive a new set of multiple options starting with A
                        if current_question["options"] and parsed_opts[0]["letter"] == 'A':
                            # Shift existing options to sub_propositions
                            current_question["sub_propositions"] = []
                            for idx_opt, opt in enumerate(current_question["options"]):
                                current_question["sub_propositions"].append({
                                    "id": idx_opt + 1,
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
                        # Use IGNORECASE to support lowercase option letters (a-e)
                        if re.match(OPTION_LOOSE_PATTERN.format(letter=first_opt["letter"]), text.strip(), re.IGNORECASE):
                            current_question["options"].extend(parsed_opts)
                    continue
                    
                # Detect K-type sub-proposition (1, 2, 3...)
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
                        current_question["_raw_text"] += "\n" + text
                elif current_question:
                    current_question["_raw_text"] += "\n" + text
                    # If there's no active clinical case, but a question is active,
                    # capture standalone image placeholders to avoid losing them
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
                # If we've already parsed and stored completed questions in questions list,
                # then this table appears at the end of the document (grid mode).
                # Otherwise, it's interleaved mode.
                is_interleaved = (len(questions) == 0)
                logger.info(f"[{filename}] Détection du mode de correction: {'Intercalé' if is_interleaved else 'Grille finale'}")

            if is_interleaved:
                # Interleaved mode: pair directly with current_question
                if current_question:
                    if current_question.get("correction") is None:
                        tc = table_corrs[0]
                        if tc.get("is_append"):
                            # If it's a multi-line column 1 append table (very rare but supported)
                            if len(questions) > 0 and questions[-1].get("correction"):
                                questions[-1]["correction"]["comment"] += "\n" + tc["comment"]
                        else:
                            current_question["correction"] = {
                                "answer_letter": tc["answer_letter"],
                                "comment": tc["comment"],
                                "correction_images": []
                            }
                            # Map correction answer letters to final option bools
                            correct_letters = re.findall(r'[A-G]', tc["answer_letter"])
                            for opt in current_question["options"]:
                                if opt["letter"] in correct_letters:
                                    opt["is_correct"] = True
                            # Auto-deduce type if multiple letters
                            if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                                current_question["question_type"] = "MULTIPLE_CHOICE"
            else:
                # Grid mode: collect and pair at the end
                grid_corrections.extend(table_corrs)
                
    # Save the last question
    if current_question:
        questions.append(current_question)
        
    logger.info(f"[{filename}] Questions détectées: {len(questions)}, Corrections trouvées en grille: {len(grid_corrections)}")
    
    # 4. Normalize question types and deduplicate options before pairing
    for question in questions:
        # 4a. K_TYPE without sub_propositions → downgrade to SINGLE/MULTIPLE_CHOICE
        if question["question_type"] == "K_TYPE" and not question.get("sub_propositions"):
            n_opts = len(question["options"])
            question["question_type"] = "MULTIPLE_CHOICE" if n_opts > 1 else "SINGLE_CHOICE"

        # 4b. Deduplicate options: if same letter appears twice (parsing artefact), keep the longer text version
        seen_letters = {}
        deduped = []
        for opt in question["options"]:
            letter = opt["letter"]
            if letter not in seen_letters:
                seen_letters[letter] = opt
                deduped.append(opt)
            else:
                # Keep the option with the longer text (more informative)
                if len(opt["text"]) > len(seen_letters[letter]["text"]):
                    idx_existing = next(i for i, o in enumerate(deduped) if o["letter"] == letter)
                    deduped[idx_existing] = opt
                    seen_letters[letter] = opt
        question["options"] = deduped

    # 5. If in grid mode, pair questions with corrections (by matching number or sequential fallback)
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
            
            # Try matching by exact question number first
            matching_corr = None
            if q_num in corr_map:
                matching_corr = corr_map[q_num]
            elif implicit_idx < len(implicit_corrs):
                # Fallback to implicit sequential list
                matching_corr = implicit_corrs[implicit_idx]
                implicit_idx += 1

            if matching_corr:
                question["correction"] = {
                    "answer_letter": matching_corr["answer_letter"],
                    "comment": matching_corr["comment"],
                    "correction_images": []
                }
                
                # Map correction answer letters to final option bools
                correct_letters = re.findall(r'[A-G]', matching_corr["answer_letter"])
                for opt in question["options"]:
                    if opt["letter"] in correct_letters:
                        opt["is_correct"] = True
                        
                # Auto-deduce type if multiple letters in standard choice
                if len(correct_letters) > 1 and question["question_type"] != "K_TYPE":
                    question["question_type"] = "MULTIPLE_CHOICE"

    # Default empty correction for any question that still doesn't have one
    for question in questions:
        if not question.get("correction"):
            question["correction"] = {
                "answer_letter": "",
                "comment": "Correction non trouvée dans le document.",
                "correction_images": []
            }
            
    # 6. Resolve image placeholders and extract image files
    for q in questions:
        # Search all fields for image references
        img_fields = ["instruction", "context"]
        if q["case_study"]:
            img_fields.append("case_study.context_text")
            
        # A list to keep track of images processed in this question
        q_image_idx = 1
        
        # 6.1 Check Case Study Context
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
