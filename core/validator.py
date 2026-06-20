"""
QCM Sanity and Structure Validation
===================================
Validates the internal JSON schema structure of parsed QCMs.
"""

def validate_qcm_structure(questions: list) -> tuple[int, list[str], dict[int, list[str]]]:
    """
    Performs basic logic and structural validations on extracted questions
    to ensure full compliance with target specifications.

    Returns:
        Tuple of (valid_count: int, errors: list[str], anomalies: dict[int, list[str]])
        where anomalies maps question_number to a list of specific anomaly tags.
    """
    valid_count = 0
    errors = []
    anomalies = {}

    required_fields = [
        "source_file", "category", "question_number", "question_type",
        "instruction", "logic_type", "has_image", "question_images",
        "sub_propositions", "options", "correction"
    ]

    import re

    for idx, q in enumerate(questions):
        q_num = q.get("question_number", 0)
        src = q.get("source_file", "?")
        q_errors = []

        # 1. Required fields
        missing = [f for f in required_fields if f not in q]
        if missing:
            err_msg = f"Q{q_num} [{src}]: Champs manquants → {missing}"
            errors.append(err_msg)
            q_errors.append("MISSING_FIELDS")
            anomalies[idx] = q_errors
            continue

        # 2. Options present
        options = q.get("options", [])
        if not options:
            errors.append(f"Q{q_num} [{src}]: Aucune option de réponse")
            q_errors.append("NO_OPTIONS")
        else:
            # 2a. Check for empty option text
            for opt in options:
                if not opt.get("text", "").strip():
                    errors.append(f"Q{q_num} [{src}]: Option {opt.get('letter')} est vide")
                    q_errors.append("EMPTY_OPTION")
                    break

            # 2b. Check for non-sequential option letters (e.g. A, B, D, E - gap found)
            letters = [opt.get("letter", "") for opt in options if opt.get("letter", "")]
            if letters:
                # Check if sorted letters form a continuous sequence starting with 'A'
                sorted_letters = sorted(letters)
                expected_letters = [chr(ord('A') + i) for i in range(len(letters))]
                if sorted_letters != expected_letters:
                    errors.append(f"Q{q_num} [{src}]: Séquence de lettres discontinue {letters}")
                    q_errors.append("SEQUENCE_GAP")

        # 3. Correction answer letter
        corr = q.get("correction") or {}
        if "answer_letter" not in corr:
            errors.append(f"Q{q_num} [{src}]: Lettre de correction manquante")
            q_errors.append("MISSING_CORRECTION")
        else:
            # 3b. Check for logic mismatch between marked option.is_correct and correction.answer_letter
            answer_letter = corr.get("answer_letter", "")
            correct_letters_in_corr = set(re.findall(r'[A-G]', answer_letter.upper()))
            correct_letters_in_opts = set(opt["letter"] for opt in options if opt.get("is_correct"))
            
            if correct_letters_in_corr != correct_letters_in_opts:
                errors.append(
                    f"Q{q_num} [{src}]: Discordance de correction (Correction: {answer_letter} vs Options marquées: {sorted(list(correct_letters_in_opts))})"
                )
                q_errors.append("LOGIC_MISMATCH")

        # 4. K-TYPE must have sub_propositions
        if q["question_type"] == "K_TYPE" and not q.get("sub_propositions"):
            errors.append(f"Q{q_num} [{src}]: K_TYPE sans sous-propositions")
            q_errors.append("KTYPE_MISSING_SUBPROPS")

        if q_errors:
            anomalies[idx] = q_errors
        else:
            valid_count += 1

    return valid_count, errors, anomalies

