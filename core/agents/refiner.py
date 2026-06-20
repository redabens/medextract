import json
import re
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from agno.agent import Agent
from core.config import get_logger
from core.llm_engine import _build_model, run_agent_with_retry

logger = get_logger("agent_refiner")

class SubPropRefinement(BaseModel):
    id: int = Field(description="Identifiant numérique de l'affirmation (1, 2, 3, etc.).")
    is_true: Optional[bool] = Field(description="Si déductible, indique si cette affirmation est scientifiquement Vraie (True) ou Fausse (False).")

class OptionRefinement(BaseModel):
    letter: str = Field(description="Lettre de l'option (A, B, C, D, E, etc.).")
    is_correct: bool = Field(description="Indique si cette option de réponse finale est exacte (True) ou non (False).")

class QuestionRefinement(BaseModel):
    q_idx: int = Field(description="Index unique de la question dans le lot fourni.")
    question_number: int = Field(description="Numéro de la question.")
    logic_type: str = Field(description="Sémantique de la consigne: 'POSITIVE' (RJ) ou 'NEGATIVE' (RF - cherche la fausse).")
    question_type: str = Field(description="SINGLE_CHOICE, MULTIPLE_CHOICE, ou K_TYPE.")
    sub_propositions: List[SubPropRefinement] = Field(default_factory=list, description="Liste des affirmations qualifiées pour K-Type.")
    options: List[OptionRefinement] = Field(description="Liste des options avec leur statut de correction mis à jour.")
    answer_letter: str = Field(description="Lettre(s) de correction finale(s) correcte(s) (ex: 'B' ou 'A, D').")

class BatchRefinementResponse(BaseModel):
    questions: List[QuestionRefinement]

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

    to_refine_indices = []
    for idx, q in enumerate(questions):
        is_ktype = q.get("question_type") == "K_TYPE" or len(q.get("sub_propositions", [])) > 0
        has_anom = idx in anomalies
        
        corr = q.get("correction") or {}
        correct_letters_in_corr = set(re.findall(r'[A-G]', corr.get("answer_letter", "").upper()))
        correct_letters_in_opts = set(opt["letter"] for opt in q.get("options", []) if opt.get("is_correct"))
        has_mismatch = (correct_letters_in_corr != correct_letters_in_opts)

        if is_ktype or has_anom or has_mismatch:
            to_refine_indices.append(idx)

    if not to_refine_indices:
        logger.info("Aucune question ne requiert de raffinage logique ou K-Type.")
        return questions

    logger.info(f"Raffinage logique et K-Type de {len(to_refine_indices)} question(s) sur {len(questions)} via Gemini...")
    agent = _create_refinement_agent()
    
    batch_size = 8
    refined_map = {}
    
    for idx in range(0, len(to_refine_indices), batch_size):
        sub_indices = to_refine_indices[idx:idx + batch_size]
        input_data = []
        for q_idx in sub_indices:
            q = questions[q_idx]
            input_data.append({
                "q_idx": q_idx,
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
                refined_map[rq.q_idx] = rq
        except Exception as e:
            logger.error(f"Erreur lors du raffinage du lot {idx // batch_size + 1}: {e}", exc_info=True)
            
    # Apply refinements to original questions list
    for idx, q in enumerate(questions):
        if idx in refined_map:
            rq = refined_map[idx]
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
