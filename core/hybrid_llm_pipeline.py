import os
from core.config import get_logger
from core.physical_dumper import dump_docx_to_raw_markdown, dump_pdf_to_raw_markdown
from core.chunker import segment_text_into_logical_chunks
from core.llm_engine import query_llm_for_structuring
from core.post_processor import normalize_question_types, deduplicate_options, resolve_image_paths
from core.agents import refine_questions_logic_and_ktype

logger = get_logger("hybrid_llm_pipeline")

def pydantic_to_dict(q) -> dict:
    """Converts a Pydantic MedExtractQuestion model to a plain dict for JSON output."""
    return q.model_dump(mode="json")

def re_anchor_math(questions: list, math_map: dict) -> list:
    """
    Replaces all math placeholders [[MATH_OMML_{hash}]] in questions' fields
    with their original LaTeX formulas.
    """
    if not math_map:
        return questions
        
    for q in questions:
        # Instruction
        if q.get("instruction"):
            for placeholder, math_val in math_map.items():
                q["instruction"] = q["instruction"].replace(placeholder, math_val)
                
        # Context
        if q.get("context"):
            for placeholder, math_val in math_map.items():
                q["context"] = q["context"].replace(placeholder, math_val)
                
        # Case study context
        if q.get("case_study") and q["case_study"].get("context_text"):
            for placeholder, math_val in math_map.items():
                q["case_study"]["context_text"] = q["case_study"]["context_text"].replace(placeholder, math_val)
                
        # Options
        for opt in q.get("options", []):
            if opt.get("text"):
                for placeholder, math_val in math_map.items():
                    opt["text"] = opt["text"].replace(placeholder, math_val)
                    
        # Sub-propositions
        for sp in q.get("sub_propositions", []):
            if sp.get("text"):
                for placeholder, math_val in math_map.items():
                    sp["text"] = sp["text"].replace(placeholder, math_val)
                    
        # Correction comment
        if q.get("correction") and q["correction"].get("comment"):
            for placeholder, math_val in math_map.items():
                q["correction"]["comment"] = q["correction"]["comment"].replace(placeholder, math_val)
                
    return questions

def run_hybrid_llm_pipeline(filepath: str, filename: str, category: str) -> list:
    """
    Full LLM-First Hybrid extraction pipeline:
      1. Physical extraction of paragraphs + math & image placeholders (physical_dumper).
      2. Semantic chunking (segment_text_into_logical_chunks).
      3. Agno LLM structuring -> validated Pydantic objects.
      4. Convert to dicts for unified JSON output.
      5. Post-Processing: re-anchoring of math formulas, image path resolution.
      6. Sémantique refinement (RJ/RF, K-Type verification).
    """
    ext = filename.lower()
    
    # 1. Physical Extraction
    logger.info(f"[Hybride LLM] Extraction physique brute de '{filename}'...")
    if ext.endswith(".docx"):
        raw_text, math_map = dump_docx_to_raw_markdown(filepath)
    elif ext.endswith(".pdf"):
        raw_text, math_map = dump_pdf_to_raw_markdown(filepath)
    else:
        logger.warning(f"[Hybride LLM] Format non supporté : {filename}")
        return []
        
    if not raw_text:
        logger.warning(f"[Hybride LLM] Aucun texte extrait de '{filename}'.")
        return []
        
    # Split text into paragraphs for chunking
    paragraphs = [p.strip() for p in raw_text.split('\n') if p.strip()]
    logger.info(f"[Hybride LLM] {len(paragraphs)} paragraphe(s) extrait(s) de '{filename}'.")
    
    # 2. Chunking
    logger.info(f"[Hybride LLM] Segmentation sémantique en chunks logiques...")
    chunks = segment_text_into_logical_chunks(paragraphs, max_questions_per_chunk=8)
    logger.info(f"[Hybride LLM] {len(chunks)} chunk(s) créé(s) pour '{filename}'.")
    
    if not chunks:
        logger.warning(f"[Hybride LLM] Aucun chunk généré pour '{filename}'.")
        return []
        
    # 3. LLM Structuring per chunk
    all_questions = []
    for chunk_idx, chunk_text in enumerate(chunks):
        logger.info(f"[Hybride LLM] Traitement chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk_text)} chars)...")
        try:
            extracted = query_llm_for_structuring(chunk_text, filename, category)
            all_questions.extend(extracted)
        except Exception as e:
            logger.error(f"[Hybride LLM] Erreur sur chunk {chunk_idx + 1}: {e}", exc_info=True)
            continue
            
    logger.info(f"[Hybride LLM] {len(all_questions)} question(s) structurée(s) par l'Agent.")
    
    # 4. Pydantic -> dict conversion
    questions_dicts = [pydantic_to_dict(q) for q in all_questions]
    
    # 5. Post-Processing & Re-anchoring
    logger.info(f"[Hybride LLM] Post-traitement et ré-ancrage des formules et images...")
    questions_dicts = re_anchor_math(questions_dicts, math_map)
    questions_dicts = resolve_image_paths(questions_dicts)
    questions_dicts = normalize_question_types(questions_dicts)
    questions_dicts = deduplicate_options(questions_dicts)
    
    # 6. Semantic Refinement (Logic types alignment)
    logger.info(f"[Hybride LLM] Raffinage final...")
    questions_dicts = refine_questions_logic_and_ktype(questions_dicts)
    
    return questions_dicts
