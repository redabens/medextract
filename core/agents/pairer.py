import re
import json
from typing import List
from pydantic import BaseModel, Field

from agno.agent import Agent
from core.config import get_logger
from core.llm_engine import _build_model, run_agent_with_retry

logger = get_logger("agent_pairer")

class PairingItem(BaseModel):
    q_idx: int = Field(description="Index unique de la question dans la liste fournie.")
    question_number: int = Field(description="Numéro de la question.")
    answer_letter: str = Field(description="Lettre(s) correcte(s) de correction.")
    comment: str = Field(description="Explication/commentaire clinique correspondant à cette question.")

class PairingResponse(BaseModel):
    pairings: List[PairingItem]

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
    for idx, q in enumerate(questions):
        questions_list.append({
            "q_idx": idx,
            "question_number": q.get("question_number"),
            "instruction": q.get("instruction")[:150] + "..." if len(q.get("instruction", "")) > 150 else q.get("instruction")
        })
        
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
                pairing_map[item.q_idx] = item
        except Exception as e:
            logger.error(f"Erreur d'appariement sémantique pour le lot {idx // batch_size + 1}: {e}", exc_info=True)
            
    # Apply paired corrections to original questions
    for idx, q in enumerate(questions):
        if idx in pairing_map:
            pair = pairing_map[idx]
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
