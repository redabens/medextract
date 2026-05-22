"""
MedExtract-API — Main Orchestrator
===================================
Dual-mode extraction pipeline:

  MODE 1 (USE_LLM = False)  →  Fast rule-based parser (Epic 01)
  MODE 2 (USE_LLM = True)   →  Agno LLM structuring engine (Epic 02)

Usage examples
--------------
  # All files in default directory, rule-based mode:
  python main.py

  # Single file, rule-based:
  python main.py --file "QCM Medicale/Ex cas clinique 01.docx"

  # Single file, LLM mode (requires OPENAI_API_KEY in .env):
  python main.py --file "QCM Medicale/Ex cas clinique 01.docx" --llm

  # Full directory in LLM mode:
  python main.py --dir "QCM Medicale" --llm

  # Override detected category:
  python main.py --file "..." --category "Pneumologie"
"""

import os
import sys
import json
import argparse

from core.config import get_logger, OUTPUT_DIR, IMAGE_DIR, USE_LLM

# ─── Rule-based parsers (Epic 01) ────────────────────────────────────────────
from core.docx_parser import parse_docx_to_qcm, extract_docx_raw_paragraphs
from core.pdf_parser  import parse_pdf_to_qcm,  extract_pdf_raw_paragraphs

# ─── LLM pipeline components (Epic 02) ────────────────────────────────────────
from core.chunker    import segment_text_into_logical_chunks
from core.llm_engine import query_llm_for_structuring, MedExtractQuestion

logger = get_logger("main_orchestrator")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def auto_deduce_category(filename: str) -> str:
    """
    Auto-deduces the medical specialty from the file name.
    Falls back to 'Médecine Générale' if nothing matches.
    """
    name_lower = filename.lower()
    if "cardio" in name_lower:
        return "Cardiologie"
    elif "hge" in name_lower:
        return "Hépato-Gastro-Entérologie"
    elif any(k in name_lower for k in ("diab", "ex 01", "ex 02", "cas clinique 0")):
        return "Diabétologie"
    elif "residanat" in name_lower:
        return "Résidanat"
    elif "pneumo" in name_lower:
        return "Pneumologie"
    elif "nephro" in name_lower:
        return "Néphrologie"
    elif "neuro" in name_lower:
        return "Neurologie"
    elif "dermato" in name_lower:
        return "Dermatologie"
    elif "gyneco" in name_lower or "obstétri" in name_lower:
        return "Gynécologie-Obstétrique"
    elif "pediatr" in name_lower or "pédiatr" in name_lower:
        return "Pédiatrie"
    elif "ortho" in name_lower or "traumato" in name_lower:
        return "Orthopédie-Traumatologie"
    return "Médecine Générale"


def validate_qcm_structure(questions: list) -> tuple:
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


def pydantic_to_dict(q: MedExtractQuestion) -> dict:
    """Converts a Pydantic MedExtractQuestion model to a plain dict for JSON output."""
    return q.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# LLM Pipeline (Epic 02)
# ─────────────────────────────────────────────────────────────────────────────

def run_llm_pipeline(filepath: str, filename: str, category: str) -> list:
    """
    Full LLM extraction pipeline for a single file:
      1. Physical extraction of paragraphs + image placeholders
      2. Semantic chunking (preserves clinical dossier integrity)
      3. Agno LLM structuring → validated Pydantic objects
      4. Convert to dicts for unified JSON output

    Returns:
        List of question dicts compatible with the rule-based output format.
    """
    ext = filename.lower()

    # ── Step 1: Raw paragraph extraction ─────────────────────────────────────
    logger.info(f"[LLM] Extraction des paragraphes bruts de '{filename}'...")
    if ext.endswith(".docx"):
        paragraphs = extract_docx_raw_paragraphs(filepath)
    elif ext.endswith(".pdf"):
        paragraphs = extract_pdf_raw_paragraphs(filepath)
    else:
        logger.warning(f"Format non supporté pour le pipeline LLM: {filename}")
        return []

    if not paragraphs:
        logger.warning(f"[LLM] Aucun paragraphe extrait de '{filename}'.")
        return []

    logger.info(f"[LLM] {len(paragraphs)} paragraphe(s) extrait(s) de '{filename}'.")

    # ── Step 2: Semantic chunking ──────────────────────────────────────────────
    logger.info(f"[LLM] Segmentation sémantique en chunks logiques...")
    chunks = segment_text_into_logical_chunks(paragraphs, max_questions_per_chunk=8)
    logger.info(f"[LLM] {len(chunks)} chunk(s) créé(s) pour '{filename}'.")

    if not chunks:
        logger.warning(f"[LLM] Aucun chunk généré pour '{filename}'.")
        return []

    # ── Step 3: LLM structuring per chunk ────────────────────────────────────
    all_questions: list[MedExtractQuestion] = []
    for chunk_idx, chunk_text in enumerate(chunks):
        logger.info(
            f"[LLM] Traitement chunk {chunk_idx + 1}/{len(chunks)} "
            f"({len(chunk_text)} chars)..."
        )
        try:
            extracted = query_llm_for_structuring(chunk_text, filename, category)
            all_questions.extend(extracted)
        except Exception as e:
            logger.error(
                f"[LLM] Erreur sur chunk {chunk_idx + 1} de '{filename}': {e}",
                exc_info=True
            )
            # Continue processing remaining chunks even if one fails
            continue

    logger.info(
        f"[LLM] '{filename}' → {len(all_questions)} question(s) structurée(s) par l'Agent Agno."
    )

    # ── Step 4: Pydantic → dict conversion ───────────────────────────────────
    return [pydantic_to_dict(q) for q in all_questions]


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    cli = argparse.ArgumentParser(
        description=(
            "MedExtract-API — Pipeline d'extraction de QCM médicaux DOCX/PDF.\n"
            "Supporte deux modes : règles hors-ligne (défaut) ou Agent Agno LLM (--llm)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    cli.add_argument("--file",     type=str, help="Chemin du fichier unique (.docx ou .pdf) à extraire.")
    cli.add_argument("--dir",      type=str, help="Chemin du dossier contenant les fichiers QCM.")
    cli.add_argument("--category", type=str, help="Catégorie médicale forcée (écrase la déduction automatique).")
    cli.add_argument(
        "--llm", action="store_true",
        help="Active le moteur de structuration IA Agno (nécessite OPENAI_API_KEY dans .env)."
    )

    args = cli.parse_args()

    # Determine effective LLM mode: config.py default OR --llm flag override
    use_llm_mode: bool = USE_LLM or args.llm

    if use_llm_mode:
        logger.info("=" * 60)
        logger.info("  MODE : Agent Agno LLM (Epic 02)")
        logger.info("=" * 60)
    else:
        logger.info("=" * 60)
        logger.info("  MODE : Parser par regles hors-ligne (Epic 01)")
        logger.info("=" * 60)

    # ── Collect input files ───────────────────────────────────────────────────
    input_file = args.file
    input_dir  = args.dir

    if not input_file and not input_dir:
        local_qcm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "QCM Medicale")
        if os.path.exists(local_qcm_dir):
            input_dir = local_qcm_dir
            logger.info(f"Aucun argument fourni — analyse du dossier par défaut : {input_dir}")
        else:
            cli.print_help()
            sys.exit(1)

    files_to_process = []
    if input_file:
        if not os.path.exists(input_file):
            logger.error(f"Fichier introuvable : {input_file}")
            sys.exit(1)
        files_to_process.append(input_file)
    elif input_dir:
        if not os.path.exists(input_dir):
            logger.error(f"Dossier introuvable : {input_dir}")
            sys.exit(1)
        for f in sorted(os.listdir(input_dir)):
            if f.lower().endswith((".docx", ".pdf")) and not f.startswith("~$"):
                files_to_process.append(os.path.join(input_dir, f))

    if not files_to_process:
        logger.warning("Aucun fichier valide (.docx ou .pdf) trouvé.")
        sys.exit(0)

    logger.info(f"Début du traitement de {len(files_to_process)} fichier(s)...")

    # ── Process files ─────────────────────────────────────────────────────────
    all_extracted_questions = []

    for filepath in files_to_process:
        filename = os.path.basename(filepath)
        category = args.category if args.category else auto_deduce_category(filename)
        ext_lower = filename.lower()

        logger.info(f"[>] Traitement : {filename}  [Categorie : {category}]")

        try:
            if use_llm_mode:
                # ── Epic 02: LLM pipeline ─────────────────────────────────
                questions = run_llm_pipeline(filepath, filename, category)
            else:
                # ── Epic 01: Rule-based pipeline ──────────────────────────
                if ext_lower.endswith(".docx"):
                    questions = parse_docx_to_qcm(filepath, category)
                elif ext_lower.endswith(".pdf"):
                    questions = parse_pdf_to_qcm(filepath, category)
                else:
                    logger.warning(f"└─ Format non supporté : {filename}")
                    continue

            all_extracted_questions.extend(questions)
            logger.info(f"[+] {len(questions)} question(s) extraite(s) de '{filename}'")

        except Exception as e:
            logger.error(f"[!] ERREUR lors du traitement de '{filename}': {e}", exc_info=True)

    # ── Validation ────────────────────────────────────────────────────────────
    valid_count, errors = validate_qcm_structure(all_extracted_questions)

    # ── Persist output ────────────────────────────────────────────────────────
    output_json_path = os.path.join(OUTPUT_DIR, "extracted_qcm.json")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(all_extracted_questions, f, ensure_ascii=False, indent=2)

    # ── Summary report ────────────────────────────────────────────────────────
    mode_label = "Agno LLM (Epic 02)" if use_llm_mode else "Regles hors-ligne (Epic 01)"
    logger.info("=" * 60)
    logger.info(f"  Extraction terminee - Mode : {mode_label}")
    logger.info(f"  Fichiers traites    : {len(files_to_process)}")
    logger.info(f"  Total questions     : {len(all_extracted_questions)}")
    logger.info(f"  Questions valides   : {valid_count}")
    logger.info(f"  Sortie JSON         : {output_json_path}")
    logger.info(f"  Dossier images      : {IMAGE_DIR}")

    if errors:
        logger.warning(f"  [!] {len(errors)} avertissement(s) de structure :")
        for err in errors[:15]:
            logger.warning(f"    - {err}")
        if len(errors) > 15:
            logger.warning(f"    ... et {len(errors) - 15} autre(s).")
    else:
        logger.info("  [OK] Aucun avertissement de structure detecte.")
    logger.info("=" * 60)

    # Exit with non-zero code if there are structural errors (useful for CI)
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
