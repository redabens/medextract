import os
import re
import fitz  # PyMuPDF
import hashlib
from core.config import (
    get_logger,
    IMAGE_DIR,
    CASE_START_REGEX,
    QUESTION_START_REGEX,
    SUB_PROP_REGEX,
    CORR_LINE_REGEX,
    INLINE_CORR_REP_REGEX,
    INLINE_CORR_FIRST_LINE_REGEX,
    INLINE_CORR_EXACT_REGEX,
    OPTION_LOOSE_PATTERN
)
from core.utils import (
    clean_text,
    generate_file_hash,
    extract_logic_type,
    parse_options_line
)
logger = get_logger("pdf_parser")

def extract_inline_correction(explanation_lines):
    """
    Given a list of explanation lines, attempts to extract the correct answer letters
    and the explanation comment.
    """
    if not explanation_lines:
        return None
        
    cleaned_lines = [line.strip() for line in explanation_lines if line.strip()]
    if not cleaned_lines:
        return None
        
    ans = None
    comment_lines = []
    
    # 1. Search all lines for an exact match of key answer markers
    for line in cleaned_lines:
        # Check for "Réponse: AB" or "Correction: D" or "Corrigé D"
        rep_match = INLINE_CORR_REP_REGEX.match(line)
        if rep_match:
            potential_ans = re.sub(r'[\s,\+]', '', rep_match.group(1)).upper()
            if potential_ans and all(c in 'ABCDE' for c in potential_ans):
                ans = potential_ans
                continue
                
        # Check if the line is exactly a letter combination, e.g. "D" or "BC"
        letters_only = re.sub(r'[\s,\+\.\)-]', '', line)
        if re.match(r'^[A-E]{1,5}$', letters_only) and len(line.strip()) <= 10:
            ans = letters_only.upper()
            continue
            
        comment_lines.append(line)
        
    if ans:
        return {
            "answer_letter": "".join(sorted(list(set(ans)))),
            "comment": "\n".join(comment_lines).strip()
        }
        
    # 2. Check if the first line starts with the answer, e.g., "D Il donne..." or "34 D - La Clinique..."
    first_line = cleaned_lines[0]
    # Match optional digits at the start, then 1-5 letters A-E, then a separator or space
    match = INLINE_CORR_FIRST_LINE_REGEX.match(first_line)
    # Also support just digits followed by 1-5 letters exactly, e.g. "27 D"
    match_exact = INLINE_CORR_EXACT_REGEX.match(first_line)
    
    if match_exact:
        ans = match_exact.group(1).upper()
        comment = ""
        if len(cleaned_lines) > 1:
            comment = "\n".join(cleaned_lines[1:])
        return {
            "answer_letter": "".join(sorted(list(set(ans)))),
            "comment": comment.strip()
        }
        
    if match:
        ans = match.group(1).upper()
        remaining_text = match.group(2).strip()
        # Ensure it's not a false positive word starting with A-E
        is_valid = (
            re.match(r'^\d+\s+', first_line) or 
            len(ans) > 1 or 
            first_line.endswith(ans) or 
            re.match(rf'^(?:\d+\s+)?{ans}\s+[\d-]', first_line, re.IGNORECASE) or
            first_line.startswith(ans + " ") or 
            first_line.startswith(ans + ".") or 
            first_line.startswith(ans + "-") or 
            first_line.startswith(ans + ":")
        )
        if is_valid:
            comment = remaining_text
            if len(cleaned_lines) > 1:
                comment += "\n" + "\n".join(cleaned_lines[1:])
            return {
                "answer_letter": "".join(sorted(list(set(ans)))),
                "comment": comment.strip()
            }
            
    return {
        "answer_letter": "",
        "comment": "\n".join(cleaned_lines).strip()
    }

def extract_pdf_media_and_text(pdf_path):
    """
    Parses a PDF file page by page using PyMuPDF.
    Extracts high-resolution images, determines their bounding boxes,
    and inserts placeholders [[IMG_ID]] into the closest text block.
    Returns a unified text representing the document.
    """
    if not os.path.exists(pdf_path):
        logger.error(f"Fichier PDF introuvable: {pdf_path}")
        return ""
        
    doc = fitz.open(pdf_path)
    file_hash = generate_file_hash(pdf_path)
    
    extracted_pages = []
    
    for page_idx, page in enumerate(doc):
        # 1. Retrieve all text blocks with their spatial coordinates (bbox)
        # Block tuple: (x0, y0, x1, y1, "text", block_no, block_type)
        text_blocks = page.get_text("blocks")
        
        # Convert to list of dicts for simpler manipulation
        blocks = []
        for b in text_blocks:
            blocks.append({
                "bbox": fitz.Rect(b[0], b[1], b[2], b[3]),
                "text": b[4],
                "type": b[6]  # 0 = text, 1 = image
            })
            
        # 2. Retrieve all images located on the current page
        image_list = page.get_images(full=True)
        
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            img_rects = page.get_image_rects(xref)
            if not img_rects:
                continue
                
            img_bbox = img_rects[0]  # Take primary coordinates
            
            # Extract raw image binary data
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
            except Exception as e:
                logger.warning(f"Impossible d'extraire l'image xref {xref} p. {page_idx+1}: {e}")
                continue
                
            # Create a unique normalized filename and save it
            unique_img_id = f"IMG_{file_hash}_P{page_idx+1}_I{img_idx+1}"
            img_filename = f"{unique_img_id}.{image_ext}"
            img_dest = os.path.join(IMAGE_DIR, img_filename)
            
            with open(img_dest, "wb") as f_img:
                f_img.write(image_bytes)
                
            # 3. Spatial Anchoring: find the text block right below the image
            best_block_idx = -1
            min_distance = float('inf')
            
            for idx, block in enumerate(blocks):
                if block["type"] == 0:  # Text block
                    # Compute vertical distance from the bottom of the image (y1) to the top of the text (y0)
                    dist_y = block["bbox"].y0 - img_bbox.y1
                    # Check if the block is below and closer than previous matches
                    if 0 <= dist_y < min_distance:
                        min_distance = dist_y
                        best_block_idx = idx
                        
            # If found, inject the placeholder at the beginning of the text block
            if best_block_idx != -1:
                blocks[best_block_idx]["text"] = f"[[{unique_img_id}]]\n" + blocks[best_block_idx]["text"]
            else:
                # Fallback: create a virtual text block at the image coordinate
                blocks.append({
                    "bbox": img_bbox,
                    "text": f"\n[[{unique_img_id}]]\n",
                    "type": 0
                })
                
        # Re-sort all blocks vertically (y0) then horizontally (x0) to maintain normal reading order
        blocks.sort(key=lambda b: (b["bbox"].y0, b["bbox"].x0))
        
        # Concat page text
        page_text = "\n".join([b["text"].strip() for b in blocks if b["text"].strip()])
        extracted_pages.append(page_text)
        
    return "\n\n--- PAGE_SEPARATOR ---\n\n".join(extracted_pages)

def parse_pdf_to_qcm(pdf_path, category):
    """
    Extracts text/images and parses QCM questions from a PDF file.
    Utilizes PyMuPDF and layout reconstruction.
    """
    filename = os.path.basename(pdf_path)
    file_hash = generate_file_hash(pdf_path)
    
    # 1. Extract raw text with anchored placeholders
    document_text = extract_pdf_media_and_text(pdf_path)
    if not document_text:
        return []
        
    lines = [clean_text(line) for line in document_text.split("\n")]
    lines = [line for line in lines if line]
    
    # Preprocess: move question numbers from the end of the line to the beginning (e.g., "toutes ces anomalies .30" -> "30. toutes ces anomalies")
    normalized_lines = []
    for line in lines:
        # Require a punctuation separator (. or ) or -) before the number to avoid matching standard numbers like "type 2" or "35"
        m_end = re.match(r'^(.*?)\s+([\.\)-])\s*(\d+)\s*$', line)
        if m_end:
            prefix_text = m_end.group(1).strip()
            q_num = m_end.group(3)
            # Only treat as a question number if it is reasonably small (less than 200)
            if int(q_num) < 200 and prefix_text and not re.match(r'^[A-E]$', prefix_text, re.IGNORECASE) and not prefix_text.isdigit():
                normalized_lines.append(f"{q_num}. {prefix_text}")
                continue
        normalized_lines.append(line)
    lines = normalized_lines
    
    # Preprocess: merge split question/correction numbers (layout artifacts)
    i = 0
    merged_lines = []
    while i < len(lines):
        # Check for 2-digit number split: digit, digit, ".", option/text
        if (i + 3 < len(lines) and 
            lines[i].isdigit() and len(lines[i]) == 1 and
            lines[i+1].isdigit() and len(lines[i+1]) == 1 and
            lines[i+2] == "." and
            (len(lines[i+3]) >= 1 and (lines[i+3][0].isupper() or lines[i+3].startswith("-")))):
            
            num = lines[i] + lines[i+1]
            opt_text = lines[i+3]
            merged_lines.append(f"{num}. {opt_text}")
            i += 4
            
        # Check for 1-digit number split: digit, ".", option/text
        elif (i + 2 < len(lines) and 
              lines[i].isdigit() and len(lines[i]) == 1 and
              lines[i+1] == "." and
              (len(lines[i+2]) >= 1 and (lines[i+2][0].isupper() or lines[i+2].startswith("-")))):
              
            num = lines[i]
            opt_text = lines[i+2]
            merged_lines.append(f"{num}. {opt_text}")
            i += 3
            
        else:
            merged_lines.append(lines[i])
            i += 1
            
    lines = merged_lines

    # Preprocess: merge table-split correction numbers and options (e.g. "1." on line i, then "B A-L'ARN..." on line i+1)
    i = 0
    merged_lines = []
    while i < len(lines):
        if (i + 1 < len(lines) and 
            re.match(r'^\d+[\.\)-]?$', lines[i]) and 
            re.match(r'^[A-E]\b', lines[i+1])):
            
            num = re.sub(r'[\.\)-]', '', lines[i])
            merged_lines.append(f"{num}- {lines[i+1]}")
            i += 2
        else:
            merged_lines.append(lines[i])
            i += 1
    lines = merged_lines
    
    # 2. Sequential parsing logic similar to docx but adapted for raw lines
    questions = []
    current_case = None
    current_question = None
    accumulated_context = []
    explanation_lines = []
    
    # Simple regex rules for extraction
    case_start_regex = CASE_START_REGEX
    question_start_regex = QUESTION_START_REGEX
    sub_prop_regex = SUB_PROP_REGEX
    corr_line_regex = CORR_LINE_REGEX
    
    # Keep track of correction section
    correction_mode = False
    corrections_raw = []
    
    for line in lines:
        if "--- PAGE_SEPARATOR ---" in line:
            continue
            
        # Detect if we reached the correction section at the end of PDF
        # Ensure we only match stand-alone correction headers, not embedded text/options
        is_corr_header = False
        is_wrap_around_trigger = False
        stripped_line = line.strip().lower()
        if re.match(r'^(?:tableau\s+de\s+)?(?:correction|corrig[eé]s?|explications?)\b', stripped_line):
            if len(stripped_line) < 40:
                is_corr_header = True
                
        # Detect transition to correction mode by wrap-around question numbering (no explicit header case)
        if not correction_mode and current_question:
            m_corr = corr_line_regex.match(line)
            if m_corr:
                corr_num = int(m_corr.group(1))
                if corr_num < 200 and corr_num < current_question["question_number"] - 5:
                    # Check if this is actually a sub-proposition for the current question (false positive)
                    is_false_positive = False
                    if sub_prop_regex.match(line) and len(current_question["options"]) == 0:
                        is_false_positive = True
                        
                    if not is_false_positive:
                        logger.info(f"[{filename}] Détection automatique de la section correction par réinitialisation du numéro ({current_question['question_number']} -> {corr_num})")
                        is_corr_header = True
                        is_wrap_around_trigger = True
                
        if is_corr_header:
            correction_mode = True
            if is_wrap_around_trigger:
                corrections_raw.append(line)
            continue
            
        if correction_mode:
            # Check if this line looks like a sequential new question starting, e.g. "61. Instruction..."
            # which would signal that we are exiting a correction block and entering a new question block
            q_match = question_start_regex.match(line)
            if q_match and current_question:
                q_num = int(q_match.group(1))
                if q_num < 200 and q_num > current_question["question_number"] and q_num <= current_question["question_number"] + 5:
                    logger.info(f"[{filename}] Détection de sortie de la section correction vers la question {q_num}")
                    correction_mode = False
            
            if correction_mode:
                # Accumulate raw correction text
                corrections_raw.append(line)
                continue
            
        # Detect end of clinical case
        if "fin du cas clinique" in line.lower():
            current_case = None
            accumulated_context = []
            continue
            
        # Detect start of clinical case
        if case_start_regex.match(line):
            current_case = {
                "case_id": hashlib.md5(line.encode('utf-8')).hexdigest()[:12],
                "case_title": line,
                "context_text": ""
            }
            accumulated_context = []
            continue
            
        # Detect start of question
        q_match = question_start_regex.match(line)
        is_legit_q = False
        if q_match:
            q_num = int(q_match.group(1))
            if q_num >= 200:
                continue
            q_instruction = clean_text(q_match.group(2))
            
            # K-Type proposition (1-5) collision check
            is_sub_prop = False
            if current_question and len(current_question["options"]) == 0 and 1 <= q_num <= 5:
                is_sub_prop = True
                
            if is_sub_prop:
                current_question["sub_propositions"].append({
                    "id": q_num,
                    "text": q_instruction,
                    "is_true": None
                })
                current_question["question_type"] = "K_TYPE"
                continue
                
            # Verify sequential validity (must be monotonic and within a window of 5)
            if current_question is None or (q_num > current_question["question_number"] and q_num <= current_question["question_number"] + 5):
                is_legit_q = True
                
        if is_legit_q:
            # Save preceding question
            if current_question:
                # Process and attach inline correction if accumulated
                if explanation_lines:
                    inline_corr = extract_inline_correction(explanation_lines)
                    if inline_corr:
                        current_question["correction"] = {
                            "answer_letter": inline_corr["answer_letter"],
                            "comment": inline_corr["comment"],
                            "correction_images": []
                        }
                        # Map correction answer letters to final option bools
                        correct_letters = re.findall(r'[A-E]', inline_corr["answer_letter"])
                        for opt in current_question["options"]:
                            if opt["letter"] in correct_letters:
                                opt["is_correct"] = True
                        if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                            current_question["question_type"] = "MULTIPLE_CHOICE"
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
                "logic_type": extract_logic_type(line),
                "has_image": False,
                "question_images": [],
                "sub_propositions": [],
                "options": [],
                "correction": None
            }
            explanation_lines = []
            continue
            
        # Detect option (A, B, C...)
        parsed_opts = parse_options_line(line)
        if not parsed_opts and current_question:
            next_letter = 'A'
            if current_question["options"]:
                last_letter = current_question["options"][-1]["letter"]
                next_letter = chr(ord(last_letter) + 1)
            loose_match = re.match(OPTION_LOOSE_PATTERN.format(letter=next_letter), line.strip())
            if loose_match:
                parsed_opts = [{
                    "letter": loose_match.group(1).upper(),
                    "text": clean_text(loose_match.group(2)),
                    "is_correct": False
                }]
                
        if parsed_opts and current_question:
            # Avoid duplicate option letters or out-of-order corrections being matched as options
            if any(opt["letter"] == o["letter"] for o in parsed_opts for opt in current_question["options"]):
                parsed_opts = []
                
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
                if re.match(OPTION_LOOSE_PATTERN.format(letter=first_opt["letter"]), line.strip()):
                    current_question["options"].extend(parsed_opts)
            continue
            
        # Detect K-type sub-proposition (1, 2, 3...)
        sub_match = sub_prop_regex.match(line)
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
            
        # Accumulate inline correction / explanation text
        if current_question:
            if len(current_question["options"]) > 0 or len(current_question["sub_propositions"]) > 0:
                explanation_lines.append(line)
                continue
                
        # Handle Clinical Case Text Updates/Context
        if current_case:
            if not current_question:
                if current_case["context_text"]:
                    current_case["context_text"] += "\n" + line
                else:
                    current_case["context_text"] = line
            else:
                accumulated_context.append(line)
                current_case["context_text"] += "\n[Mise à jour] " + line
                
    # Save the last question
    if current_question:
        if explanation_lines:
            inline_corr = extract_inline_correction(explanation_lines)
            if inline_corr:
                current_question["correction"] = {
                    "answer_letter": inline_corr["answer_letter"],
                    "comment": inline_corr["comment"],
                    "correction_images": []
                }
                correct_letters = re.findall(r'[A-E]', inline_corr["answer_letter"])
                for opt in current_question["options"]:
                    if opt["letter"] in correct_letters:
                        opt["is_correct"] = True
                if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                    current_question["question_type"] = "MULTIPLE_CHOICE"
        questions.append(current_question)
        
    # 3. Parse corrections from raw text block sequentially
    corrections = []
    # Simple regex to extract letter keys and comments from raw correction lines
    corr_line_regex = re.compile(r'^(?:[qQ](?:[uU][eE][sS][tT][iI][oO][nN])?\s*)?(\d+)[\s\.:-]+([A-E]{1,5})(?:\s*[\.:-]\s*|\s+|$)(.*)')
    
    current_comment = []
    current_ans = ""
    current_num = -1
    
    for c_line in corrections_raw:
        m = corr_line_regex.match(c_line)
        if m:
            # If we had a previous correction, save it
            if current_num != -1:
                corrections.append({
                    "num": current_num,
                    "answer_letter": current_ans,
                    "comment": "\n".join(current_comment).strip()
                })
            current_num = int(m.group(1))
            current_ans = m.group(2)
            current_comment = [m.group(3)]
        else:
            if current_num != -1:
                current_comment.append(c_line)
                
    # Save the last correction
    if current_num != -1:
        corrections.append({
            "num": current_num,
            "answer_letter": current_ans,
            "comment": "\n".join(current_comment).strip()
        })
        
    # If no structured corrections were parsed, attempt to parse simple grids like "1. A  2. B  3. C"
    if not corrections:
        grid_matches = re.findall(r'\b(?:Q)?(\d+)[\s\.:-]+([A-E]{1,5})\b', "\n".join(corrections_raw))
        for g in grid_matches:
            corrections.append({
                "num": int(g[0]),
                "answer_letter": g[1],
                "comment": "Explication non détaillée."
            })
            
    logger.info(f"[{filename}] PDF Questions détectées: {len(questions)}, Corrections trouvées: {len(corrections)}")
    
    # Sort corrections by their number for sequential mapping
    corrections.sort(key=lambda x: x["num"])
    
    # 4. Pair questions with corrections (sequential or by matching number)
    for idx, question in enumerate(questions):
        # Find correction by question number if possible, or fall back to index matching
        matching_corr = None
        for corr in corrections:
            if corr["num"] == question["question_number"]:
                matching_corr = corr
                break
                
        if not matching_corr and idx < len(corrections):
            matching_corr = corrections[idx]
            
        if matching_corr:
            # Overwrite only if question correction is not yet populated OR has no answer letters
            if not question.get("correction") or not question["correction"].get("answer_letter"):
                question["correction"] = {
                    "answer_letter": matching_corr["answer_letter"],
                    "comment": matching_corr["comment"],
                    "correction_images": []
                }
                
                # Map correction answer letters to final option bools
                correct_letters = re.findall(r'[A-E]', matching_corr["answer_letter"])
                for opt in question["options"]:
                    if opt["letter"] in correct_letters:
                        opt["is_correct"] = True
                        
                # Auto-deduce type if multiple letters in standard choice
                if len(correct_letters) > 1 and question["question_type"] != "K_TYPE":
                    question["question_type"] = "MULTIPLE_CHOICE"
            else:
                # Merge or prefer tail correction comment if it's richer and not generic
                if matching_corr["comment"] and matching_corr["comment"] != "Explication non détaillée.":
                    if not question["correction"].get("comment") or len(matching_corr["comment"]) > len(question["correction"]["comment"]):
                        question["correction"]["comment"] = matching_corr["comment"]
        else:
            if not question.get("correction"):
                question["correction"] = {
                    "answer_letter": "",
                    "comment": "Correction non trouvée dans le PDF.",
                    "correction_images": []
                }
            
    # 5. Finalize PDF image references
    for q in questions:
        # Search all fields for images references [[IMG_...]]
        # If an image placeholder is present, add it to question_images list and set has_image to True
        img_placeholders = []
        if q["instruction"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["instruction"]))
        if q["context"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["context"]))
        if q["case_study"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["case_study"]["context_text"]))
            
        # Also check options
        for opt in q["options"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', opt["text"]))
            
        if img_placeholders:
            # Populate images list
            q["has_image"] = True
            # Find extension for these images in IMAGE_DIR
            for placeholder in set(img_placeholders):
                found_filename = None
                for ext in [".png", ".jpg", ".jpeg", ".gif"]:
                    test_file = f"{placeholder}{ext}"
                    if os.path.exists(os.path.join(IMAGE_DIR, test_file)):
                        found_filename = test_file
                        break
                if found_filename:
                    q["question_images"].append(found_filename)
                else:
                    # Append default placeholder extension
                    q["question_images"].append(f"{placeholder}.png")
                    
    return questions


def extract_pdf_raw_paragraphs(pdf_path: str) -> list:
    """
    Extracts a flat list of raw text paragraphs from a PDF file for use by
    the LLM pipeline (Epic 02). Image extraction and [[IMG_...]] placeholder
    injection are handled by the existing extract_pdf_media_and_text() function.

    Args:
        pdf_path: Absolute path to the .pdf file.

    Returns:
        List of non-empty cleaned text line strings.
    """
    filename = os.path.basename(pdf_path)

    # Reuse the existing physical extraction (images + spatial anchoring)
    raw_full_text = extract_pdf_media_and_text(pdf_path)

    if not raw_full_text:
        return []

    paragraphs_out = []
    for line in raw_full_text.split('\n'):
        line = line.strip()
        # Skip internal page separator markers
        if not line or line == "--- PAGE_SEPARATOR ---":
            continue
        paragraphs_out.append(line)

    logger.info(f"[{filename}] {len(paragraphs_out)} paragraphes extraits pour le pipeline LLM.")
    return paragraphs_out
