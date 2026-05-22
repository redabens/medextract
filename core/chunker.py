import re
from typing import List
from core.config import get_logger, CASE_START_REGEX, QUESTION_START_REGEX

logger = get_logger("semantic_chunker")

def segment_text_into_logical_chunks(paragraphs: List[str], max_questions_per_chunk: int = 8) -> List[str]:
    """
    Groups a sequential list of paragraphs into self-contained text chunks.
    Ensures that a clinical dossier/case study statement and all its associated 
    questions are kept together in a single chunk to preserve logical context.
    
    Args:
        paragraphs: List of cleaned paragraphs from the document.
        max_questions_per_chunk: For isolated questions, maximum questions to group together.
        
    Returns:
        List of merged text chunks ready to be processed by the LLM.
    """
    chunks = []
    current_chunk_paragraphs = []
    
    in_case_study = False
    question_count_in_current_chunk = 0
    
    for idx, p in enumerate(paragraphs):
        p_clean = p.strip()
        if not p_clean:
            continue
            
        # Detect starts of dossiers/cases and questions using centralized regexes
        is_new_case = bool(CASE_START_REGEX.match(p_clean))
        is_question = bool(QUESTION_START_REGEX.match(p_clean))
        
        # Decide if we need to split and start a new chunk
        should_split = False
        
        # Case 1: We hit a new Case Study, and we have accumulated previous content
        if is_new_case and current_chunk_paragraphs:
            should_split = True
            logger.debug(f"Splitting chunk at paragraph {idx}: New Clinical Case detected.")
            
        # Case 2: We hit a question, and we've reached the batch size for isolated questions
        elif is_question:
            question_count_in_current_chunk += 1
            # Only split if we are NOT in a clinical dossier (isolated questions)
            if not in_case_study and question_count_in_current_chunk > max_questions_per_chunk:
                should_split = True
                logger.debug(f"Splitting chunk at paragraph {idx}: Max question count ({max_questions_per_chunk}) reached for isolated block.")
                
        if should_split:
            # Save current accumulated chunk
            chunk_text = "\n\n".join(current_chunk_paragraphs).strip()
            if chunk_text:
                chunks.append(chunk_text)
            
            # Reset chunk tracking variables
            current_chunk_paragraphs = []
            question_count_in_current_chunk = 0
            if is_question:
                question_count_in_current_chunk = 1
                
        # Track active block state
        if is_new_case:
            in_case_study = True
        elif is_question and not in_case_study:
            # We hit an independent question, so we are no longer in a case study context
            in_case_study = False
            
        current_chunk_paragraphs.append(p)
        
    # Append the last remaining chunk
    if current_chunk_paragraphs:
        chunk_text = "\n\n".join(current_chunk_paragraphs).strip()
        if chunk_text:
            chunks.append(chunk_text)
            
    logger.info(f"Segmented {len(paragraphs)} paragraphs into {len(chunks)} ssemantic logical chunks.")
    return chunks
