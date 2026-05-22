import os
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

from agno.agent import Agent
from core.config import get_logger, LLM_MODEL, LLM_PROVIDER, GOOGLE_API_KEY, OPENAI_API_KEY

logger = get_logger("llm_engine")

# =====================================================================
# Modeles Pydantic v2 conformes au Cahier des Specifications
# =====================================================================

class SubProposition(BaseModel):
    id: int = Field(description="Identifiant numerique de la sous-proposition (ex: 1, 2, 3, 4).")
    text: str = Field(description="Contenu scientifique de l'affirmation.")
    is_true: Optional[bool] = Field(
        default=None,
        description="Si deductible, indique si cette sous-proposition specifique est scientifiquement vraie ou fausse."
    )

class Option(BaseModel):
    letter: Literal["A", "B", "C", "D", "E"] = Field(description="Lettre de l'option (A, B, C, D ou E).")
    text: str = Field(description="Contenu textuel de la proposition de reponse (ex: '1 + 3' ou 'Pneumonie a pneumocoque').")
    is_correct: bool = Field(description="Indique si cette option de reponse finale est correcte (True) ou non (False).")

class Correction(BaseModel):
    answer_letter: str = Field(description="La ou les lettres correctes de correction (ex: 'B' ou 'A, C').")
    comment: Optional[str] = Field(default=None, description="Explication et justification medicale detaillee de la correction.")
    correction_images: List[str] = Field(
        default_factory=list,
        description="Liste des placeholders d'images [[IMG_...]] associes a la justification de correction."
    )

class CaseStudy(BaseModel):
    case_id: str = Field(description="Hash ou ID unique identifiant le dossier clinique.")
    case_title: str = Field(description="Titre ou numero du dossier (ex: 'Dossier 03' ou 'Cas clinique 2').")
    context_text: str = Field(description="Texte complet de l'enonce clinique de base.")

class MedExtractQuestion(BaseModel):
    source_file: str = Field(description="Nom du fichier source ayant servi a l'extraction.")
    category: str = Field(description="Discipline medicale ou specialite (ex: Cardiologie, Pneumologie).")
    case_study: Optional[CaseStudy] = Field(
        default=None,
        description="Informations sur le cas clinique parent si applicable, sinon null."
    )
    context: Optional[str] = Field(
        default=None,
        description="Enonce clinique ou contexte direct de la question (identique au context_text du cas clinique)."
    )
    question_number: int = Field(description="Numero d'ordre de la question dans le document source.")
    question_type: Literal["SINGLE_CHOICE", "MULTIPLE_CHOICE", "K_TYPE"] = Field(
        description="Type de question : SINGLE_CHOICE, MULTIPLE_CHOICE, ou K_TYPE (affirmations combinees)."
    )
    instruction: str = Field(description="Enonce direct de la question ou consigne posee.")
    logic_type: Literal["POSITIVE", "NEGATIVE"] = Field(
        description="POSITIVE si on recherche les reponses VRAIES (RJ), NEGATIVE si on recherche la reponse FAUSSE/l'intrus (RF)."
    )
    has_image: bool = Field(description="Indique si l'enonce contient au moins une image.")
    question_images: List[str] = Field(
        default_factory=list,
        description="Liste des placeholders d'images [[IMG_...]] presents dans l'enonce."
    )
    sub_propositions: List[SubProposition] = Field(
        default_factory=list,
        description="Affirmations numerotees de base (principalement pour les K-Type)."
    )
    options: List[Option] = Field(
        description="Les 5 propositions finales de reponses (A, B, C, D, E) proposees a l'etudiant."
    )
    correction: Correction = Field(description="Donnees de correction et explications.")

class QCMBatchResponse(BaseModel):
    """Conteneur pour forcer l'Agent Agno a retourner une liste d'objets MedExtractQuestion."""
    questions: List[MedExtractQuestion]

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
            "   - Pour les QCM directs (A, B, C, D, E sans affirmations intermediaires), laissez 'sub_propositions' vide.",
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
        response = agent.run(prompt)
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
