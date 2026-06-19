import os
import json
import re
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from agno.agent import Agent
from core.config import get_logger, LLM_MODEL, LLM_PROVIDER
from core.llm_engine import _build_model, run_agent_with_retry

logger = get_logger("hybrid_refiner")

# =====================================================================
# Pydantic schemas for micro-agents structured outputs
# =====================================================================

class SubPropRefinement(BaseModel):
    id: int = Field(description="Identifiant numérique de l'affirmation (1, 2, 3, etc.).")
    is_true: Optional[bool] = Field(description="Si déductible, indique si cette affirmation est scientifiquement Vraie (True) ou Fausse (False).")

class OptionRefinement(BaseModel):
    letter: str = Field(description="Lettre de l'option (A, B, C, D, E, etc.).")
    is_correct: bool = Field(description="Indique si cette option de réponse finale est exacte (True) ou non (False).")

class QuestionRefinement(BaseModel):
    question_number: int = Field(description="Numéro de la question.")
    logic_type: str = Field(description="Sémantique de la consigne: 'POSITIVE' (RJ) ou 'NEGATIVE' (RF - cherche la fausse).")
    question_type: str = Field(description="SINGLE_CHOICE, MULTIPLE_CHOICE, ou K_TYPE.")
    sub_propositions: List[SubPropRefinement] = Field(default_factory=list, description="Liste des affirmations qualifiées pour K-Type.")
    options: List[OptionRefinement] = Field(description="Liste des options avec leur statut de correction mis à jour.")
    answer_letter: str = Field(description="Lettre(s) de correction finale(s) correcte(s) (ex: 'B' ou 'A, D').")

class BatchRefinementResponse(BaseModel):
    questions: List[QuestionRefinement]

class OptionItem(BaseModel):
    letter: str = Field(description="Lettre de l'option (ex: 'A', 'B', etc.).")
    text: str = Field(description="Texte de la proposition de réponse.")
    is_correct: bool = Field(description="Indique si cette option de réponse finale est correcte.")

class QuestionSalvageResponse(BaseModel):
    instruction: str = Field(description="L'énoncé nettoyé de la question.")
    options: List[OptionItem] = Field(description="Toutes les propositions de réponses extraites.")
    answer_letter: str = Field(description="Lettre(s) correcte(s) de correction.")
    comment: str = Field(description="Commentaire clinique explicatif.")

class PairingItem(BaseModel):
    question_number: int = Field(description="Numéro de la question.")
    answer_letter: str = Field(description="Lettre(s) correcte(s) de correction.")
    comment: str = Field(description="Explication/commentaire clinique correspondant à cette question.")

class PairingResponse(BaseModel):
    pairings: List[PairingItem]


# =====================================================================
# Micro-agents helper creators
# =====================================================================

def _create_refinement_agent() -> Agent:
    model = _build_model()
    return Agent(
        model=model,
        description="Vous êtes un enseignant en médecine spécialisé dans la correction et la validation de QCM.",
        instructions=[
            "1. Analysez le texte des questions, leurs options et surtout le commentaire clinique de correction.",
            "2. Alignez le champ logic_type: 'POSITIVE' si on cherche les réponses Vraies (RJ), 'NEGATIVE' si on cherche la réponse Fausse / l'intrus (RF). Attention aux doubles négations.",
            "3. Identifiez le type de question (SINGLE_CHOICE, MULTIPLE_CHOICE, K_TYPE). Un K-Type est une question à double niveau avec des affirmations 1, 2, 3, 4 et des options qui combinent ces affirmations.",
            "4. Pour les K-Types, déduisez le statut is_true de chaque sous-proposition en analysant le commentaire de correction.",
            "5. Mettez à jour le statut is_correct de chaque option de réponse finale pour être en accord parfait avec le commentaire de correction et la lettre de correction (answer_letter)."
        ],
        output_schema=BatchRefinementResponse,
        markdown=False
    )

def _create_salvage_agent() -> Agent:
    model = _build_model()
    return Agent(
        model=model,
        description="Vous êtes un expert en extraction et structuration de QCM médicaux mal formatés ou tronqués.",
        instructions=[
            "1. Analysez le texte brut fourni contenant une question médicale et ses options de réponses.",
            "2. Séparez proprement l'énoncé de la question (instruction) des options de réponses.",
            "3. Extrayez toutes les options de réponses (A, B, C...) présentes sans contrainte fixe sur le nombre (il peut y en avoir 4, 5 ou plus).",
            "4. Renseignez la lettre correcte de correction et le commentaire s'ils sont trouvables dans le texte brut."
        ],
        output_schema=QuestionSalvageResponse,
        markdown=False
    )

def _create_pairing_agent() -> Agent:
    model = _build_model()
    return Agent(
        model=model,
        description="Vous êtes un expert en réalignement de bases de données de QCM médicaux.",
        instructions=[
            "1. Vous recevez une liste d'énoncés de questions et un bloc de texte de corrections désorganisé.",
            "2. Associez chaque question à sa bonne lettre de réponse et son explication clinique correspondante en effectuant une recherche sémantique fine.",
            "3. Si une question n'a aucune correction correspondante dans le bloc, indiquez-le en laissant la lettre vide et en ajoutant 'Correction non trouvée' dans le commentaire."
        ],
        output_schema=PairingResponse,
        markdown=False
    )



# =====================================================================
# Main Refinement Functions
# =====================================================================

def refine_questions_logic_and_ktype(questions: List[dict], anomalies: Dict[int, List[str]] = None) -> List[dict]:
    """
    Refines logical types (RJ/RF) and K-type assertions statuses (is_true)
    for a list of questions, ensuring absolute consistency between answer letters
    and options correctness.
    """
    if not questions:
        return []
        
    if anomalies is None:
        anomalies = {}

    # Filter questions: we only refine questions that:
    # 1. are K_TYPE (or have sub-propositions)
    # 2. have anomalies (e.g. LOGIC_MISMATCH, SEQUENCE_GAP, etc.)
    # 3. have an option mismatch vs answer letters
    to_refine = []
    for q in questions:
        q_num = q.get("question_number")
        is_ktype = q.get("question_type") == "K_TYPE" or len(q.get("sub_propositions", [])) > 0
        has_anom = q_num in anomalies
        
        corr = q.get("correction") or {}
        correct_letters_in_corr = set(re.findall(r'[A-G]', corr.get("answer_letter", "").upper()))
        correct_letters_in_opts = set(opt["letter"] for opt in q.get("options", []) if opt.get("is_correct"))
        has_mismatch = (correct_letters_in_corr != correct_letters_in_opts)

        if is_ktype or has_anom or has_mismatch:
            to_refine.append(q)

    if not to_refine:
        logger.info("Aucune question ne requiert de raffinage logique ou K-Type.")
        return questions

    logger.info(f"Raffinage logique et K-Type de {len(to_refine)} question(s) sur {len(questions)} via Gemini...")
    agent = _create_refinement_agent()
    
    # We send questions in small batches to fit context limits and preserve precision
    batch_size = 8
    refined_map = {}
    
    for idx in range(0, len(to_refine), batch_size):
        sub_list = to_refine[idx:idx + batch_size]
        # Clean input for LLM to reduce size
        input_data = []
        for q in sub_list:
            input_data.append({
                "question_number": q.get("question_number"),
                "instruction": q.get("instruction"),
                "question_type": q.get("question_type"),
                "options": [{"letter": o["letter"], "text": o["text"]} for o in q.get("options", [])],
                "sub_propositions": [{"id": sp["id"], "text": sp["text"]} for sp in q.get("sub_propositions", [])],
                "correction": {
                    "answer_letter": q.get("correction", {}).get("answer_letter", ""),
                    "comment": q.get("correction", {}).get("comment", "")
                }
            })
            
        prompt = (
            "Voici un lot de QCM médicaux extraits. Veuillez raffiner leur logique, qualifier les types de questions "
            "et corriger/aligner le statut exact de véracité des options et des affirmations K-Type en vous basant "
            "sur leurs corrections.\n\n"
            f"DONNÉES EXTRAITES :\n{json.dumps(input_data, ensure_ascii=False, indent=2)}"
        )
        
        try:
            res = run_agent_with_retry(agent, prompt)
            content = res.content
            
            if isinstance(content, BatchRefinementResponse):
                batch_res = content
            elif isinstance(content, str):
                raw = json.loads(content)
                batch_res = BatchRefinementResponse.model_validate(raw)
            else:
                logger.error(f"Format de réponse LLM invalide: {type(content)}")
                continue
                
            for rq in batch_res.questions:
                refined_map[rq.question_number] = rq
        except Exception as e:
            logger.error(f"Erreur lors du raffinage du lot {idx // batch_size + 1}: {e}", exc_info=True)
            
    # Apply refinements to original questions list
    for q in questions:
        q_num = q.get("question_number")
        if q_num in refined_map:
            rq = refined_map[q_num]
            q["logic_type"] = rq.logic_type
            q["question_type"] = rq.question_type
            
            # Align K-type sub-propositions is_true statuses
            if rq.sub_propositions and q.get("sub_propositions"):
                sub_ref_map = {sp.id: sp.is_true for sp in rq.sub_propositions}
                for sp in q["sub_propositions"]:
                    if sp["id"] in sub_ref_map:
                        sp["is_true"] = sub_ref_map[sp["id"]]
                        
            # Update options correctness
            if rq.options and q.get("options"):
                opt_ref_map = {opt.letter: opt.is_correct for opt in rq.options}
                for opt in q["options"]:
                    if opt["letter"] in opt_ref_map:
                        opt["is_correct"] = opt_ref_map[opt["letter"]]
                        
            # Update answer letter in correction
            if rq.answer_letter and q.get("correction"):
                q["correction"]["answer_letter"] = rq.answer_letter
                
    return questions


def salvage_failed_questions(questions: List[dict], anomalies: Dict[int, List[str]], raw_text_by_q: Dict[int, str]) -> List[dict]:
    """
    Salvages questions that failed structural validation by sending the raw text
    of the failed question section to Gemini for precise boundary and option rebuilding.
    """
    if not questions or not anomalies:
        return questions
        
    logger.info(f"Rattrapage ciblé de {len(anomalies)} question(s) en anomalie...")
    agent = _create_salvage_agent()
    
    # Create index mapping
    q_map = {q.get("question_number"): q for q in questions}
    
    for q_num, anomaly_list in anomalies.items():
        # Check if the anomalies require options salvage
        salvage_needed = any(a in ("SEQUENCE_GAP", "EMPTY_OPTION", "NO_OPTIONS") for a in anomaly_list)
        if not salvage_needed:
            continue
            
        # Get raw text snippet representing the question
        snippet = raw_text_by_q.get(q_num)
        if not snippet and q_num in q_map:
            q_old = q_map[q_num]
            snippet = q_old.get("_raw_text")
            if not snippet:
                # Fallback snippet built from parsed elements
                opt_strs = [f"{o['letter']}. {o['text']}" for o in q_old.get("options", [])]
                snippet = f"Question {q_num}.\n{q_old.get('instruction')}\n" + "\n".join(opt_strs)
                if q_old.get("correction"):
                    snippet += f"\nCorrection: {q_old['correction'].get('answer_letter')}\n{q_old['correction'].get('comment')}"
                
        logger.info(f"Appel du salvage agent pour la Question N°{q_num}...")
        prompt = (
            f"Veuillez restructurer proprement cette question médicale en anomalie :\n\n"
            f"TEXTE BRUT ANORMAL :\n{snippet}"
        )
        
        try:
            res = run_agent_with_retry(agent, prompt)
            content = res.content
            
            if isinstance(content, QuestionSalvageResponse):
                salvage_res = content
            elif isinstance(content, str):
                raw = json.loads(content)
                salvage_res = QuestionSalvageResponse.model_validate(raw)
            else:
                continue
                
            if q_num in q_map:
                q = q_map[q_num]
                q["instruction"] = salvage_res.instruction
                
                # Rebuild options
                new_opts = []
                for opt in salvage_res.options:
                    new_opts.append({
                        "letter": opt.letter.upper(),
                        "text": opt.text,
                        "is_correct": opt.is_correct
                    })
                q["options"] = new_opts
                
                # Check option counts
                q_opts_len = len(new_opts)
                if q_opts_len > 1:
                    q["question_type"] = "MULTIPLE_CHOICE" if any(o["is_correct"] for o in new_opts) else "SINGLE_CHOICE"
                
                # Update correction
                if salvage_res.answer_letter and q.get("correction"):
                    q["correction"]["answer_letter"] = salvage_res.answer_letter
                    if salvage_res.comment:
                        q["correction"]["comment"] = salvage_res.comment
                        
                logger.info(f"Question N°{q_num} récupérée avec succès ! ({len(new_opts)} option(s) extraite(s))")
        except Exception as e:
            logger.error(f"Erreur de récupération pour la Question N°{q_num}: {e}", exc_info=True)
            
    return list(q_map.values())


def semantic_pair_corrections(questions: List[dict], raw_corrections: List[str]) -> List[dict]:
    """
    Pairs questions with corrections using semantic correlation.
    Prevents shift cascades.
    """
    if not questions or not raw_corrections:
        return questions
        
    logger.info(f"Appariement sémantique intelligent de {len(questions)} question(s) et {len(raw_corrections)} corrections...")
    agent = _create_pairing_agent()
    
    questions_list = []
    for q in questions:
        questions_list.append({
            "question_number": q.get("question_number"),
            "instruction": q.get("instruction")[:150] + "..." if len(q.get("instruction", "")) > 150 else q.get("instruction")
        })
        
    # Batch size for pairing to prevent context blowup
    batch_size = 15
    pairing_map = {}
    
    for idx in range(0, len(questions), batch_size):
        sub_q = questions_list[idx:idx + batch_size]
        prompt = (
            "Associez sémantiquement les questions suivantes à leurs bonnes réponses cliniques "
            "présentes dans le bloc brut de corrections. Ne décalez jamais les questions.\n\n"
            f"QUESTIONS À ASSOCIER :\n{json.dumps(sub_q, ensure_ascii=False, indent=2)}\n\n"
            f"BLOC BRUT DE CORRECTIONS :\n" + "\n".join(raw_corrections)
        )
        
        try:
            res = run_agent_with_retry(agent, prompt)
            content = res.content
            
            if isinstance(content, PairingResponse):
                pair_res = content
            elif isinstance(content, str):
                raw = json.loads(content)
                pair_res = PairingResponse.model_validate(raw)
            else:
                continue
                
            for item in pair_res.pairings:
                pairing_map[item.question_number] = item
        except Exception as e:
            logger.error(f"Erreur d'appariement sémantique pour le lot {idx // batch_size + 1}: {e}", exc_info=True)
            
    # Apply paired corrections to original questions
    for q in questions:
        q_num = q.get("question_number")
        if q_num in pairing_map:
            pair = pairing_map[q_num]
            if not q.get("correction"):
                q["correction"] = {"answer_letter": "", "comment": "", "correction_images": []}
            q["correction"]["answer_letter"] = pair.answer_letter
            q["correction"]["comment"] = pair.comment
            
            # Map correction answer letters to final option bools
            correct_letters = set(re.findall(r'[A-G]', pair.answer_letter.upper()))
            for opt in q.get("options", []):
                opt["is_correct"] = (opt["letter"] in correct_letters)
                
            # Auto-deduce choice type
            if len(correct_letters) > 1 and q["question_type"] != "K_TYPE":
                q["question_type"] = "MULTIPLE_CHOICE"
                
    return questions
