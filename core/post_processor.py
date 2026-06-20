import os
import re
from core.config import IMAGE_DIR, get_logger

logger = get_logger("post_processor")

def normalize_question_types(questions: list) -> list:
    """
    K_TYPE without sub_propositions -> downgrade to SINGLE_CHOICE or MULTIPLE_CHOICE.
    """
    for q in questions:
        if q.get("question_type") == "K_TYPE" and not q.get("sub_propositions"):
            n_opts = len(q.get("options", []))
            q["question_type"] = "MULTIPLE_CHOICE" if n_opts > 1 else "SINGLE_CHOICE"
    return questions

def deduplicate_options(questions: list) -> list:
    """
    Deduplicate options: if the same option letter appears twice, keep the longer text.
    """
    for q in questions:
        seen_letters = {}
        deduped = []
        for opt in q.get("options", []):
            letter = opt["letter"].upper()
            if letter not in seen_letters:
                seen_letters[letter] = opt
                deduped.append(opt)
            else:
                if len(opt["text"]) > len(seen_letters[letter]["text"]):
                    idx_existing = next(i for i, o in enumerate(deduped) if o["letter"] == letter)
                    deduped[idx_existing] = opt
                    seen_letters[letter] = opt
        q["options"] = deduped
    return questions

def resolve_image_paths(questions: list) -> list:
    """
    Cleans brackets [[ ]] from question and correction images, maps them to their correct
    extensions in IMAGE_DIR, and synchronizes the has_image flag.
    """
    for q in questions:
        # 1. Resolve question_images
        cleaned_q_imgs = []
        for img in q.get("question_images", []):
            img_clean = re.sub(r'[\[\]\s]', '', img)
            if not img_clean:
                continue
            
            found_filename = None
            for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
                base, _ = os.path.splitext(img_clean)
                test_file = f"{base}{ext}"
                if os.path.exists(os.path.join(IMAGE_DIR, test_file)):
                    found_filename = test_file
                    break
            
            if found_filename:
                cleaned_q_imgs.append(found_filename)
            else:
                base, ext = os.path.splitext(img_clean)
                if not ext:
                    img_clean = f"{img_clean}.png"
                cleaned_q_imgs.append(img_clean)
        
        q["question_images"] = cleaned_q_imgs

        # 2. Resolve correction_images
        corr = q.get("correction") or {}
        cleaned_c_imgs = []
        for img in corr.get("correction_images", []):
            img_clean = re.sub(r'[\[\]\s]', '', img)
            if not img_clean:
                continue
            
            found_filename = None
            for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
                base, _ = os.path.splitext(img_clean)
                test_file = f"{base}{ext}"
                if os.path.exists(os.path.join(IMAGE_DIR, test_file)):
                    found_filename = test_file
                    break
            
            if found_filename:
                cleaned_c_imgs.append(found_filename)
            else:
                base, ext = os.path.splitext(img_clean)
                if not ext:
                    img_clean = f"{img_clean}.png"
                cleaned_c_imgs.append(img_clean)
        
        corr["correction_images"] = cleaned_c_imgs
        q["correction"] = corr

        # 3. Auto-sync has_image
        if q["question_images"] or corr["correction_images"]:
            q["has_image"] = True

    return questions
