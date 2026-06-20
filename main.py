"""
MedExtract-API — Main Orchestrator
===================================
Orchestration of medical QCM extraction.

Usage examples
--------------
  # Rule-based offline mode:
  python main.py --file "QCM Medicale/Uploaded/Qcms.docx" --rules

  # Rules-First Hybrid mode:
  python main.py --file "QCM Medicale/Uploaded/Qcms.docx" --hybrid-rules

  # LLM-First Hybrid mode:
  python main.py --file "QCM Medicale/Uploaded/Qcms.docx" --hybrid-llm
"""

import os
import sys
import json
import argparse

from core.config import get_logger, OUTPUT_DIR, IMAGE_DIR

# ─── Offline Rule-based parsers ──────────────────────────────────────────────
from core.docx import parse_docx_to_qcm
from core.pdf  import parse_pdf_to_qcm

# ─── Category & Validation helpers ───────────────────────────────────────────
from core.category import auto_deduce_category
from core.validator import validate_qcm_structure

# ─── Hybrid Orchestrators ────────────────────────────────────────────────────
from core.hybrid_rules_pipeline import run_hybrid_rules_pipeline
from core.hybrid_llm_pipeline import run_hybrid_llm_pipeline

logger = get_logger("main_orchestrator")


def main():
    cli = argparse.ArgumentParser(
        description=(
            "MedExtract-API — Pipeline d'extraction de QCM médicaux DOCX/PDF.\n"
            "Supporte trois modes : règles locales, hybride rules-first (coopératif) ou hybride llm-first."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    cli.add_argument("--file",     type=str, help="Chemin du fichier unique (.docx ou .pdf) à extraire.")
    cli.add_argument("--dir",      type=str, help="Chemin du dossier contenant les fichiers QCM.")
    cli.add_argument("--category", type=str, help="Catégorie médicale forcée (écrase la déduction automatique).")
    
    # Modes selection
    cli.add_argument(
        "--rules", action="store_true",
        help="Active le parser déterministe local par règles hors-ligne."
    )
    cli.add_argument(
        "--hybrid-rules", action="store_true",
        help="Active la pipeline Hybride Rules-First (parser local + micro-rattrapage Gemini)."
    )
    cli.add_argument(
        "--hybrid-llm", action="store_true",
        help="Active la pipeline Hybride LLM-First (extraction brute + structuration IA + ré-ancrage)."
    )
    
    # Backward compatibility mapping
    cli.add_argument(
        "--hybrid", action="store_true",
        help="Alias de --hybrid-rules"
    )

    args = cli.parse_args()

    # Determine mode
    mode = "rules"
    if args.hybrid_llm:
        mode = "hybrid-llm"
    elif args.hybrid_rules or args.hybrid:
        mode = "hybrid-rules"
    elif args.rules:
        mode = "rules"
    else:
        # Default mode is hybrid-rules if none specified
        mode = "hybrid-rules"

    logger.info("=" * 60)
    if mode == "hybrid-llm":
        logger.info("  MODE : Hybride Auto-Adaptatif (LLM-First) [Robuste]")
    elif mode == "hybrid-rules":
        logger.info("  MODE : Hybride Coopératif (Rules-First) [Par défaut]")
    else:
        logger.info("  MODE : Parser par règles hors-ligne")
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
            if mode == "hybrid-llm":
                questions = run_hybrid_llm_pipeline(filepath, filename, category)
            elif mode == "hybrid-rules":
                questions = run_hybrid_rules_pipeline(filepath, filename, category)
            else:
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
    valid_count, errors, anomalies = validate_qcm_structure(all_extracted_questions)

    # ── Persist output ────────────────────────────────────────────────────────
    output_json_path = os.path.join(OUTPUT_DIR, "extracted_qcm.json")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(all_extracted_questions, f, ensure_ascii=False, indent=2)

    # ── Summary report ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"  Extraction terminee - Mode : {mode}")
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

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
