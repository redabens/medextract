"""
QCM Sanity and Structure Validation
===================================
Validates the internal JSON schema structure of parsed QCMs.
"""

def validate_qcm_structure(questions: list) -> tuple[int, list[str]]:
    """
    Performs basic logic and structural validations on extracted questions
    to ensure full compliance with target specifications.

    Returns:
        Tuple of (valid_count: int, errors: list[str])
    """
    valid_count = 0
    errors = []

    required_fields = [
        "source_file", "category", "question_number", "question_type",
        "instruction", "logic_type", "has_image", "question_images",
        "sub_propositions", "options", "correction"
    ]

    for q in questions:
        q_num = q.get("question_number", "?")
        src   = q.get("source_file", "?")

        # 1. Required fields
        missing = [f for f in required_fields if f not in q]
        if missing:
            errors.append(f"Q{q_num} [{src}]: Champs manquants → {missing}")
            continue

        # 2. Options present
        if not q.get("options"):
            errors.append(f"Q{q_num} [{src}]: Aucune option de réponse")
            continue

        # 3. Correction answer letter
        if "answer_letter" not in (q.get("correction") or {}):
            errors.append(f"Q{q_num} [{src}]: Lettre de correction manquante")
            continue

        # 4. K-TYPE must have sub_propositions
        if q["question_type"] == "K_TYPE" and not q.get("sub_propositions"):
            errors.append(f"Q{q_num} [{src}]: K_TYPE sans sous-propositions")
            continue

        valid_count += 1

    return valid_count, errors
