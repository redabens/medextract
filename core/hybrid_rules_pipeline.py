import os
from core.config import get_logger
from core.docx.parser import parse_docx_to_qcm
from core.pdf.parser import parse_pdf_to_qcm
from core.validator import validate_qcm_structure
from core.agents import salvage_failed_questions, refine_questions_logic_and_ktype

logger = get_logger("hybrid_rules_pipeline")

def run_hybrid_rules_pipeline(filepath: str, filename: str, category: str) -> list:
    """
    Hybrid cooperative extraction pipeline (Rules-First):
      1. Parse locally using rules-based offline parser (DOCX/PDF).
      2. Validate structure to identify question-specific anomalies.
      3. Call targeted LLM salvager to rebuild any failed or incomplete options.
      4. Call targeted LLM refiner to qualification logically (RJ/RF) and K-type assertions.
    """
    ext_lower = filename.lower()
    
    # 1. Parse locally (fast, local, anchors images & formulas)
    logger.info(f"[Hybride Rules] Étape 1 : Parsing local déterministe de '{filename}'...")
    if ext_lower.endswith(".docx"):
        questions = parse_docx_to_qcm(filepath, category)
    elif ext_lower.endswith(".pdf"):
        questions = parse_pdf_to_qcm(filepath, category)
    else:
        logger.warning(f"[Hybride Rules] Format non supporté : {filename}")
        return []
        
    if not questions:
        logger.warning(f"[Hybride Rules] Aucune question extraite localement de '{filename}'.")
        return []
        
    # 2. Validate to detect structural anomalies
    valid_count, errors, anomalies = validate_qcm_structure(questions)
    
    # 3. Targeted Salvage if anomalies found
    if anomalies:
        logger.info(f"[Hybride Rules] Étape 2 : {len(anomalies)} anomalie(s) détectée(s). Lancement du rattrapage IA...")
        questions = salvage_failed_questions(questions, anomalies, {})
        # Re-validate
        valid_count, errors, anomalies = validate_qcm_structure(questions)
    else:
        logger.info("[Hybride Rules] Étape 2 : Structure locale valide. Aucun rattrapage requis.")
        
    # 4. Refine logical types, K-types, and correct options alignment
    logger.info(f"[Hybride Rules] Étape 3 : Raffinage sémantique fin (logic_type, K-Type)...")
    questions = refine_questions_logic_and_ktype(questions, anomalies)
    
    return questions
