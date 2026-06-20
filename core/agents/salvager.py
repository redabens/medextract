import re
import json
from typing import List, Dict
from pydantic import BaseModel, Field

from agno.agent import Agent
from core.config import get_logger
from core.llm_engine import _build_model, run_agent_with_retry

logger = get_logger("agent_salvager")

class OptionItem(BaseModel):
    letter: str = Field(description="Lettre de l'option (ex: 'A', 'B', etc.).")
    text: str = Field(description="Texte de la proposition de réponse.")
    is_correct: bool = Field(description="Indique si cette option de réponse finale est correcte.")

class QuestionSalvageResponse(BaseModel):
    instruction: str = Field(description="L'énoncé nettoyé de la question.")
    options: List[OptionItem] = Field(description="Toutes les propositions de réponses extraites.")
    answer_letter: str = Field(description="Lettre(s) correcte(s) de correction.")
    comment: str = Field(description="Commentaire clinique explicatif.")

def _create_salvage_agent() -> Agent:
    model = _build_model()
    return Agent(
        model=model,
        description="Vous êtes un expert en extraction et structuration de QCM médicaux mal formatés ou tronqués.",
        instructions=[
            "1. Analysez le texte brut fourni contenant une question médicale et ses options de réponses.",
            "2. Séparez proprement l'énoncé de la question (instruction) des options de réponses.",
            "3. Extrayez toutes les options de réponses (A, B, C...) présentes sans contrainte fixe sur le nombre (il peut y en avoir 4, 5 ou plus).",
            "4. Renseignez la lettre correcte de correction et le commentaire s'ils sont trouvables dans le texte brut.",
            "5. Si le QCM est combinatoire (les options A, B, C, D, E combinent des affirmations numérotées 1, 2, 3, 4, 5), vous devez extraire les options finales (A, B, C, D, E) comme options. Les affirmations numérotées (1 à 5) doivent être intégrées dans l'énoncé (instruction). N'utilisez JAMAIS de chiffres (1, 2, 3...) comme lettres d'options (letter). Les lettres d'options doivent TOUJOURS être alphabétiques (A, B, C, D, E, etc.)."
        ],
        output_schema=QuestionSalvageResponse,
        markdown=False
    )

def salvage_failed_questions(questions: List[dict], anomalies: Dict[int, List[str]], raw_text_by_q: Dict[int, str]) -> List[dict]:
    """
    Salvages questions that failed structural validation by sending the raw text
    of the failed question section to Gemini for precise boundary and option rebuilding.
    """
    if not questions or not anomalies:
        return questions
        
    logger.info(f"Rattrapage ciblé de {len(anomalies)} question(s) en anomalie...")
    agent = _create_salvage_agent()
    
    for idx, anomaly_list in anomalies.items():
        if idx >= len(questions):
            continue
            
        salvage_needed = any(a in ("SEQUENCE_GAP", "EMPTY_OPTION", "NO_OPTIONS") for a in anomaly_list)
        if not salvage_needed:
            continue
            
        q = questions[idx]
        q_num = q.get("question_number")
        
        snippet = raw_text_by_q.get(q_num)
        if not snippet:
            snippet = q.get("_raw_text")
            if not snippet:
                opt_strs = [f"{o['letter']}. {o['text']}" for o in q.get("options", [])]
                snippet = f"Question {q_num}.\n{q.get('instruction')}\n" + "\n".join(opt_strs)
                if q.get("correction"):
                    snippet += f"\nCorrection: {q['correction'].get('answer_letter')}\n{q['correction'].get('comment')}"
                
        logger.info(f"Appel du salvage agent pour la Question N°{q_num} (index {idx})...")
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
                
            q["instruction"] = salvage_res.instruction
            
            new_opts = []
            for opt in salvage_res.options:
                new_opts.append({
                    "letter": opt.letter.upper(),
                    "text": opt.text,
                    "is_correct": opt.is_correct
                })
            q["options"] = new_opts
            
            q_opts_len = len(new_opts)
            if q_opts_len > 1:
                q["question_type"] = "MULTIPLE_CHOICE" if any(o["is_correct"] for o in new_opts) else "SINGLE_CHOICE"
            
            orig_corr = q.get("correction", {})
            orig_ans = orig_corr.get("answer_letter", "").strip()
            
            is_valid_salvage_ans = bool(re.match(r'^[A-G](?:\s*,\s*[A-G])*$', salvage_res.answer_letter.strip().upper()))
            
            if is_valid_salvage_ans or not orig_ans or "Non trouv" in orig_ans or orig_ans == "Non prcis":
                if salvage_res.answer_letter and q.get("correction"):
                    q["correction"]["answer_letter"] = salvage_res.answer_letter
                    if salvage_res.comment:
                        q["correction"]["comment"] = salvage_res.comment
                        
            logger.info(f"Question N°{q_num} (index {idx}) récupérée avec succès ! ({len(new_opts)} option(s) extraite(s))")
        except Exception as e:
            logger.error(f"Erreur de récupération pour la Question N°{q_num}: {e}", exc_info=True)
            
    return questions
