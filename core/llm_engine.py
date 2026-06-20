import os
import time
import json
from typing import List, Optional, Literal

from agno.agent import Agent
from core.config import get_logger, LLM_MODEL, LLM_PROVIDER, GOOGLE_API_KEY, OPENAI_API_KEY
from core.models import MedExtractQuestion, QCMBatchResponse

logger = get_logger("llm_engine")

# =====================================================================
# Fabrique de modele LLM (agnostique du fournisseur)
# =====================================================================

def _build_model():
    """
    Construit et retourne le modele LLM Agno en fonction de LLM_PROVIDER.
    Supporte : 'google' (Gemini), 'openai' (GPT).
    """
    provider = LLM_PROVIDER.lower()

    if provider == "google":
        from agno.models.google import Gemini
        if not GOOGLE_API_KEY:
            raise ValueError(
                "La variable GOOGLE_API_KEY est manquante. "
                "Ajoutez GOOGLE_API_KEY=votre_cle dans le fichier '.env'."
            )
        logger.info(f"Fournisseur LLM : Google Gemini ({LLM_MODEL})")
        return Gemini(id=LLM_MODEL, api_key=GOOGLE_API_KEY)

    elif provider == "openai":
        from agno.models.openai import OpenAIChat
        if not OPENAI_API_KEY:
            raise ValueError(
                "La variable OPENAI_API_KEY est manquante. "
                "Ajoutez OPENAI_API_KEY=votre_cle dans le fichier '.env'."
            )
        logger.info(f"Fournisseur LLM : OpenAI ({LLM_MODEL})")
        return OpenAIChat(id=LLM_MODEL, api_key=OPENAI_API_KEY)

    else:
        raise ValueError(
            f"Fournisseur LLM non supporte : '{LLM_PROVIDER}'. "
            "Valeurs valides : 'google', 'openai'."
        )

# =====================================================================
# Initialisation de l'Agent Agno (singleton)
# =====================================================================

_structuring_agent: Optional[Agent] = None

def get_structuring_agent() -> Agent:
    """
    Retourne l'Agent Agno singleton configure pour la structuration de QCM medicaux.
    Le modele utilise est determine par LLM_PROVIDER dans core/config.py.
    """
    global _structuring_agent
    if _structuring_agent is not None:
        return _structuring_agent

    model = _build_model()

    _structuring_agent = Agent(
        model=model,
        description="Vous etes un medecin enseignant et un expert en structuration de bases de donnees medicales francophones (EDN / Residanat).",
        instructions=[
            "1. LOGIQUE DE QUESTION (logic_type) :",
            "   - Categorizez chaque question en logic_type = 'POSITIVE' (Reponse Juste - RJ) ou 'NEGATIVE' (Reponse Fausse - RF).",
            "   - Le type 'NEGATIVE' s'applique des que la consigne recherche l'intruse, l'affirmation incorrecte, ou exclut un diagnostic.",
            "   - Mots-cles indicateurs : 'fausse', 'sauf', 'incorrecte', 'a l exclusion de', 'eliminer', 'ne fait pas partie'.",
            "",
            "2. STRUCTURES COMPLEXES (K-TYPE) :",
            "   - Si le texte liste des affirmations numerotees (1, 2, 3, 4) suivies d options de combinaisons (A=1+2, B=2+3...),",
            "     renseignez ces affirmations dans 'sub_propositions'.",
            "   - Deduisez et marquez la veracite de chaque sous-proposition ('is_true': true/false) via la correction fournie.",
            "   - Pour les QCM directs (A-G sans affirmations intermediaires), laissez 'sub_propositions' vide.",
            "",
            "3. PROTECTION DES PLACEHOLDERS D IMAGES :",
            "   - Conservez EXACTEMENT et sans modification les placeholders [[IMG_xxxx_Qxx]] ou [[IMG_xxxx_Pxx_Ixx]].",
            "   - Listez-les dans 'question_images' ou 'correction_images' selon leur emplacement.",
            "   - Positionnez 'has_image' a True si au moins un placeholder est present.",
            "",
            "4. CONSERVATION DES NOTATIONS SCIENTIFIQUES :",
            "   - Conservez les formules (PaO2, PaCO2), unites (mmHg, g/L) et symboles LaTeX/Unicode exactement.",
            "",
            "5. INTEGRITE DE LA CORRECTION :",
            "   - Remplissez 'correction.comment' avec la justification clinique complete en francais.",
            "   - Assurez une concordance absolue entre 'correction.answer_letter' et le drapeau 'is_correct' de chaque option."
        ],
        output_schema=QCMBatchResponse,   # Agno 2.x: Pydantic schema for structured JSON output
        markdown=False
    )

    logger.info(f"Agent Agno initialise (provider={LLM_PROVIDER}, model={LLM_MODEL}).")
    return _structuring_agent


def run_agent_with_retry(agent: Agent, prompt: str, max_retries: int = 5, initial_delay: float = 4.0) -> any:
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            # Pacing sleep to stay under rate limits (15 RPM is ~4.0s per request)
            time.sleep(4.2)
            res = agent.run(prompt)
            
            # Inspect response content for rate limit codes returned inside JSON
            content = res.content
            if content:
                err_dict = None
                if isinstance(content, dict):
                    err_dict = content
                elif isinstance(content, str):
                    try:
                        err_dict = json.loads(content)
                    except Exception:
                        pass
                
                if isinstance(err_dict, dict) and "error" in err_dict:
                    err_info = err_dict["error"]
                    if isinstance(err_info, dict):
                        code = err_info.get("code")
                        msg = err_info.get("message", "")
                        if code == 429 or "429" in msg or "exhausted" in msg.lower() or "limit" in msg.lower():
                            raise RuntimeError(f"API Rate Limit Error 429: {msg}")
            return res
        except Exception as e:
            err_str = str(e)
            
            # Check if this is a daily quota exhaustion for gemini-2.5-flash
            if "2.5-flash" in err_str and ("quota" in err_str.lower() or "limit" in err_str.lower() or "exhausted" in err_str.lower()):
                logger.warning(
                    "⚠️ Quota journalier épuisé pour gemini-2.5-flash ! Bascule automatique de l'agent sur gemini-flash-lite-latest..."
                )
                try:
                    from agno.models.google import Gemini
                    global LLM_MODEL
                    LLM_MODEL = "gemini-flash-lite-latest"
                    agent.model = Gemini(id="gemini-flash-lite-latest", api_key=GOOGLE_API_KEY)
                    # Reset delay and retry immediately
                    delay = initial_delay
                    continue
                except Exception as fallback_err:
                    logger.error(f"Échec lors de la bascule vers gemini-flash-lite-latest : {fallback_err}")
            
            is_rate_limit = ("429" in err_str or "limit" in err_str.lower() or "exhausted" in err_str.lower() or "resource" in err_str.lower())
            if attempt < max_retries - 1:
                if is_rate_limit:
                    logger.warning(
                        f"Limite de requêtes atteinte (429). Attente de {delay}s "
                        f"avant essai {attempt + 1}/{max_retries}..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.warning(f"Erreur API ({err_str}). Essai {attempt + 1}/{max_retries} dans {delay}s...")
                    time.sleep(delay)
                    delay *= 1.5
            else:
                # Raise the error on the last attempt so we don't return an invalid schema
                logger.error(f"Échec définitif de l'appel agent après {max_retries} essais : {err_str}")
                raise e

# =====================================================================
# Exécuteur de requêtes IA
# =====================================================================

def query_llm_for_structuring(chunk_text: str, source_filename: str, category: str) -> List[MedExtractQuestion]:
    """
    Transmet un chunk textuel a l'Agent Agno et retourne une liste de QCM structures et valides.

    Args:
        chunk_text: Contenu textuel brut pre-segmente contenant un cas clinique ou un lot de questions.
        source_filename: Nom du fichier d'origine pour tracabilite.
        category: Specialite medicale associee.

    Returns:
        Liste d'objets MedExtractQuestion valides.
    """
    agent = get_structuring_agent()

    prompt = (
        f"--- CONTEXTE D'EXTRACTION ---\n"
        f"Fichier source : {source_filename}\n"
        f"Categorie medicale : {category}\n\n"
        f"--- TEXTE BRUT DE QCM A STRUCTURER ---\n"
        f"{chunk_text}"
    )

    logger.info(f"Envoi du chunk ({len(chunk_text)} chars) a l'Agent Agno...")

    try:
        response = run_agent_with_retry(agent, prompt)
        content = response.content

        # Agno with output_model returns the Pydantic object directly in content
        if isinstance(content, QCMBatchResponse):
            batch_response = content
        elif isinstance(content, str):
            # Fallback: parse raw JSON string if content is a string
            import json
            raw = json.loads(content)
            batch_response = QCMBatchResponse.model_validate(raw)
        else:
            raise ValueError(f"Type de reponse inattendu de l'Agent Agno : {type(content)}")

        questions = batch_response.questions
        logger.info(f"Extraction reussie : {len(questions)} question(s) via Agno.")

        # Post-inject source_file and category to guarantee consistency
        for q in questions:
            q.source_file = source_filename
            q.category = category

        return questions

    except Exception as e:
        logger.error(f"Erreur lors de la structuration par l'Agent Agno: {e}", exc_info=True)
        raise e
