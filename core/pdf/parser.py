import os
import re
import hashlib
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
from core.pdf.extractor import extract_pdf_media_and_text
from core.pdf.preprocessor import normalize_pdf_lines
from core.pdf.corrections import extract_inline_correction, parse_grid_corrections
from core.post_processor import normalize_question_types, deduplicate_options

logger = get_logger("pdf_parser")

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
        
    raw_lines = [clean_text(line) for line in document_text.split("\n") if line.strip()]
    
    # 2. Clean and preprocess lines (split/merge normalizations)
    lines = normalize_pdf_lines(raw_lines)
    
    questions = []
    current_case = None
    current_question = None
    accumulated_context = []
    explanation_lines = []
    detected_separator = None
    
    case_start_regex = CASE_START_REGEX
    question_start_regex = QUESTION_START_REGEX
    sub_prop_regex = SUB_PROP_REGEX
    
    # Identify index where corrections block starts
    sep_idx = -1
    for idx_l, line in enumerate(lines):
        if re.search(r'^(?:CORRECTION|CORRIG[EÉ]|EXPLICATIONS|REPONSES|R[EÉ]PONSES)\b', line, re.IGNORECASE):
            sep_idx = idx_l
            detected_separator = line
            break
            
    questions_raw = lines
    corrections_raw = []
    
    if sep_idx != -1:
        logger.info(f"[{filename}] Séparateur de corrections détecté à la ligne {sep_idx} : '{detected_separator}'")
        questions_raw = lines[:sep_idx]
        corrections_raw = lines[sep_idx+1:]
        
    # Main state-machine parsing loop
    for line in questions_raw:
        if "fin du cas clinique" in line.lower():
            current_case = None
            accumulated_context = []
            continue
            
        if case_start_regex.match(line):
            current_case = {
                "case_id": hashlib.md5(line.encode('utf-8')).hexdigest()[:12],
                "case_title": line,
                "context_text": ""
            }
            accumulated_context = []
            continue
            
        q_match = question_start_regex.match(line)
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
                    current_question["_raw_text"] += "\n" + line
                continue
                
            # Save previous question
            if current_question:
                if explanation_lines:
                    inline_corr = extract_inline_correction(explanation_lines)
                    if inline_corr:
                        current_question["correction"] = {
                            "answer_letter": inline_corr["answer_letter"],
                            "comment": inline_corr["comment"],
                            "correction_images": []
                        }
                        correct_letters = re.findall(r'[A-G]', inline_corr["answer_letter"])
                        for opt in current_question["options"]:
                            if opt["letter"] in correct_letters:
                                opt["is_correct"] = True
                        if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                            current_question["question_type"] = "MULTIPLE_CHOICE"
                questions.append(current_question)
                
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
                "correction": None,
                "_raw_text": line
            }
            explanation_lines = []
            continue
            
        parsed_opts = parse_options_line(line)
        if not parsed_opts and current_question:
            next_letter = 'A'
            if current_question["options"]:
                last_letter = current_question["options"][-1]["letter"]
                next_letter = chr(ord(last_letter) + 1)
            loose_match = re.match(
                OPTION_LOOSE_PATTERN.format(letter=next_letter), line.strip()
            ) or re.match(
                OPTION_LOOSE_PATTERN.format(letter=next_letter.lower()), line.strip(), re.IGNORECASE
            )
            if loose_match:
                parsed_opts = [{
                    "letter": loose_match.group(1).upper(),
                    "text": clean_text(loose_match.group(2)),
                    "is_correct": False
                }]
                
        if parsed_opts and current_question:
            current_question["_raw_text"] += "\n" + line
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
                if re.match(OPTION_LOOSE_PATTERN.format(letter=first_opt["letter"]), line.strip(), re.IGNORECASE):
                    current_question["options"].extend(parsed_opts)
            continue
            
        sub_match = sub_prop_regex.match(line)
        if sub_match and current_question and len(current_question["options"]) == 0:
            current_question["_raw_text"] += "\n" + line
            sub_id = int(sub_match.group(1))
            sub_text = clean_text(sub_match.group(2))
            current_question["sub_propositions"].append({
                "id": sub_id,
                "text": sub_text,
                "is_true": None
            })
            current_question["question_type"] = "K_TYPE"
            continue
            
        if current_question:
            if len(current_question["options"]) > 0 or len(current_question["sub_propositions"]) > 0:
                explanation_lines.append(line)
                current_question["_raw_text"] += "\n" + line
                continue
                
        if current_case:
            if not current_question:
                if current_case["context_text"]:
                    current_case["context_text"] += "\n" + line
                else:
                    current_case["context_text"] = line
            else:
                accumulated_context.append(line)
                current_case["context_text"] += "\n[Mise à jour] " + line
                current_question["_raw_text"] += "\n" + line
                
    # Save last question
    if current_question:
        if explanation_lines:
            inline_corr = extract_inline_correction(explanation_lines)
            if inline_corr:
                current_question["correction"] = {
                    "answer_letter": inline_corr["answer_letter"],
                    "comment": inline_corr["comment"],
                    "correction_images": []
                }
                correct_letters = re.findall(r'[A-G]', inline_corr["answer_letter"])
                for opt in current_question["options"]:
                    if opt["letter"] in correct_letters:
                        opt["is_correct"] = True
                if len(correct_letters) > 1 and current_question["question_type"] != "K_TYPE":
                    current_question["question_type"] = "MULTIPLE_CHOICE"
        questions.append(current_question)
        
    # 3. Parse grid corrections
    corrections = parse_grid_corrections(corrections_raw)
    logger.info(f"[{filename}] PDF Questions détectées: {len(questions)}, Corrections trouvées: {len(corrections)}")
    
    # 4. Shared Normalization & Deduplication
    questions = normalize_question_types(questions)
    questions = deduplicate_options(questions)

    # Sort corrections for sequential fallback mapping
    corrections.sort(key=lambda x: x["num"])
    
    # 5. Pair questions with corrections
    for idx, question in enumerate(questions):
        matching_corr = None
        for corr in corrections:
            if corr["num"] == question["question_number"]:
                matching_corr = corr
                break
                
        if not matching_corr and idx < len(corrections):
            matching_corr = corrections[idx]
            
        if matching_corr:
            if not question.get("correction") or not question["correction"].get("answer_letter"):
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
            else:
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
            
    # 6. Finalize PDF image references
    for q in questions:
        img_placeholders = []
        if q["instruction"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["instruction"]))
        if q["context"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["context"]))
        if q["case_study"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["case_study"]["context_text"]))
            
        for opt in q["options"]:
            img_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', opt["text"]))
            
        if img_placeholders:
            q["has_image"] = True
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
                    q["question_images"].append(f"{placeholder}.png")
                    
        corr_placeholders = []
        if q["correction"] and q["correction"].get("comment"):
            corr_placeholders.extend(re.findall(r'\[\[(IMG_[a-f0-9]+_P\d+_I\d+)\]\]', q["correction"]["comment"]))
            
        if corr_placeholders:
            q["has_image"] = True
            if "correction_images" not in q["correction"]:
                q["correction"]["correction_images"] = []
            for placeholder in set(corr_placeholders):
                found_filename = None
                for ext in [".png", ".jpg", ".jpeg", ".gif"]:
                    test_file = f"{placeholder}{ext}"
                    if os.path.exists(os.path.join(IMAGE_DIR, test_file)):
                        found_filename = test_file
                        break
                if found_filename:
                    q["correction"]["correction_images"].append(found_filename)
                else:
                    q["correction"]["correction_images"].append(f"{placeholder}.png")
                    
    return questions
