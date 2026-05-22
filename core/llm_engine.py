import os
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from core.config import get_logger, LLM_MODEL, OPENAI_API_KEY

logger = get_logger("llm_engine")

# =====================================================================
# Modèles Pydantic v2 conformes au Cahier des Spécifications
# =====================================================================

class SubProposition(BaseModel):
    id: int = Field(description="Identifiant numérique de la sous-proposition (ex: 1, 2, 3, 4).")
    text: str = Field(description="Contenu scientifique de l'affirmation.")
    is_true: Optional[bool] = Field(
        default=None, 
        description="Si déductible, indique si cette sous-proposition spécifique est scientifiquement vraie ou fausse."
    )

class Option(BaseModel):
    letter: Literal["A", "B", "C", "D", "E"] = Field(description="Lettre de l'option (A, B, C, D ou E).")
    text: str = Field(description="Contenu textuel de la proposition de réponse (ex: '1 + 3' ou 'Pneumonie à pneumocoque').")
    is_correct: bool = Field(description="Indique si cette option de réponse finale est correcte (True) ou non (False).")

class Correction(BaseModel):
    answer_letter: str = Field(description="La ou les lettres correctes de correction (ex: 'B' ou 'A, C').")
    comment: Optional[str] = Field(default=None, description="Explication et justification médicale détaillée de la correction.")
    correction_images: List[str] = Field(
        default_factory=list, 
        description="Liste des placeholders d'images [[IMG_...]] associés à la justification de correction."
    )

class CaseStudy(BaseModel):
    case_id: str = Field(description="Hash ou ID unique identifiant le dossier clinique.")
    case_title: str = Field(description="Titre ou numéro du dossier (ex: 'Dossier 03' ou 'Cas clinique 2').")
    context_text: str = Field(description="Texte complet de l'énoncé clinique de base.")

class MedExtractQuestion(BaseModel):
    source_file: str = Field(description="Nom du fichier source ayant servi à l'extraction.")
    category: str = Field(description="Discipline médicale ou spécialité (ex: Cardiologie, Pneumologie).")
    case_study: Optional[CaseStudy] = Field(
        default=None, 
        description="Informations sur le cas clinique parent si applicable, sinon null."
    )
    context: Optional[str] = Field(
        default=None, 
        description="Énoncé clinique ou contexte direct de la question (identique au context_text du cas clinique)."
    )
    question_number: int = Field(description="Numéro d'ordre de la question dans le document source.")
    question_type: Literal["SINGLE_CHOICE", "MULTIPLE_CHOICE", "K_TYPE"] = Field(
        description="Type de question : SINGLE_CHOICE (Choix unique), MULTIPLE_CHOICE (Choix multiples standard), ou K_TYPE (Affirmations combinées)."
    )
    instruction: str = Field(description="Énoncé direct de la question ou consigne posée.")
    logic_type: Literal["POSITIVE", "NEGATIVE"] = Field(
        description="POSITIVE si on recherche les réponses VRAIES (RJ), NEGATIVE si on recherche la réponse FAUSSE/l'intrus (RF)."
    )
    has_image: bool = Field(description="Indique si l'énoncé contient au moins une image.")
    question_images: List[str] = Field(
        default_factory=list, 
        description="Liste des placeholders d'images [[IMG_...]] présents dans l'énoncé."
    )
    sub_propositions: List[SubProposition] = Field(
        default_factory=list, 
        description="Affirmations numérotées de base (utilisé principalement pour les K-Type, laisser vide pour les QCM classiques)."
    )
    options: List[Option] = Field(
        description="Les 5 propositions finales de réponses (A, B, C, D, E) proposées à l'étudiant."
    )
    correction: Correction = Field(description="Données de correction et explications.")

class QCMBatchResponse(BaseModel):
    """Conteneur pour forcer l'Agent Agno à retourner une liste d'objets MedExtractQuestion."""
    questions: List[MedExtractQuestion]

# =====================================================================
# Initialisation de l'Agent Agno
# =====================================================================

_structuring_agent: Optional[Agent] = None

def get_structuring_agent() -> Agent:
    """
    Retourne l'Agent Agno singleton configuré pour la structuration de QCM médicaux.
    """
    global _structuring_agent
    if _structuring_agent is not None:
        return _structuring_agent
        
    if not OPENAI_API_KEY or "your_openai_api_key" in OPENAI_API_KEY:
        raise ValueError(
            "La variable OPENAI_API_KEY est manquante ou non valide. "
            "Veuillez configurer votre clé réelle dans le fichier '.env' à la racine."
        )
        
    logger.info(f"Initialisation de l'Agent Agno de Structuration (Modèle : {LLM_MODEL})...")
    
    # Configuration du modèle de chat OpenAI avec la clé d'API
    model = OpenAIChat(id=LLM_MODEL, api_key=OPENAI_API_KEY)
    
    _structuring_agent = Agent(
        model=model,
        description="Vous êtes un médecin enseignant et un expert en structuration de bases de données médicales francophones (EDN / Résidanat).",
        instructions=[
            "1. LOGIQUE DE QUESTION (logic_type) :",
            "   - Vous devez catégoriser chaque question en logic_type = 'POSITIVE' (Réponse Juste - RJ) ou 'NEGATIVE' (Réponse Fausse - RF).",
            "   - Le type 'NEGATIVE' s'applique dès que la consigne recherche l'intruse, l'affirmation incorrecte, ou exclut un diagnostic.",
            "   - Mots-clés indicateurs : 'fausse', 'sauf', 'incorrecte', 'à l'exclusion de', 'éliminer', 'ne fait pas partie'.",
            "   - Soyez vigilant face aux formulations négatives multiples.",
            "",
            "2. STRUCTURES COMPLEXES (K-TYPE) :",
            "   - Si le texte liste des affirmations numérotées (1, 2, 3, 4) suivies d'options de combinaisons (A=1+2, B=2+3...),",
            "     renseignez ces affirmations dans 'sub_propositions'.",
            "   - Déduisez et marquez la véracité de chaque sous-proposition ('is_true': true/false) à partir de la correction fournie.",
            "   - Pour les QCM directs (A, B, C, D, E sans affirmations intermédiaires), laissez 'sub_propositions' vide.",
            "",
            "3. PROTECTION ET ANCRAGE DES PLACEHOLDERS D'IMAGES :",
            "   - Le texte source contient des placeholders d'images de type [[IMG_xxxx_Qxx]] ou [[IMG_xxxx_Pxx_Ixx]].",
            "   - Vous devez les laisser EXACTEMENT intacts et inchangés dans le texte du 'context_text', 'context' ou 'instruction'.",
            "   - Listez ces placeholders exacts dans 'question_images' (ou 'correction_images' s'ils apparaissent dans la justification).",
            "   - Positionnez le booléen 'has_image' à True si au moins un placeholder d'image est présent.",
            "",
            "4. CONSERVATION DES NOTATIONS SCIENTIFIQUES :",
            "   - Conservez les formules de gaz du sang (PaO2, PaCO2), de clairance, les abréviations, unités (mmHg, g/L) et symboles (\\uparrow, \\downarrow, \\pm) en LaTeX ou Unicode.",
            "",
            "5. INTÉGRITÉ DE LA CORRECTION :",
            "   - Remplissez le champ 'correction.comment' avec la justification clinique complète en français. Conservez sa richesse.",
            "   - Assurez-vous d'une concordance absolue à 100% entre 'correction.answer_letter' (ex: 'B' ou 'A, C') et le drapeau 'is_correct' de chaque option du tableau 'options'."
        ],
        response_format=QCMBatchResponse,
        temperature=0.0,
        markdown=False
    )
    
    return _structuring_agent

# =====================================================================
# Exécuteur de requêtes IA
# =====================================================================

def query_llm_for_structuring(chunk_text: str, source_filename: str, category: str) -> List[MedExtractQuestion]:
    """
    Transmet un chunk textuel à l'Agent Agno et retourne une liste de QCM hautement structurés et validés.
    
    Args:
        chunk_text: Contenu textuel brut pré-segmenté contenant un cas clinique ou un lot de questions.
        source_filename: Nom du fichier d'origine pour traçabilité.
        category: Spécialité médicale associée.
        
    Returns:
        Liste d'objets MedExtractQuestion validés.
    """
    agent = get_structuring_agent()
    
    prompt = f"""
    --- CONTEXTE D'EXTRACTION ---
    Fichier source : {source_filename}
    Catégorie médicale : {category}
    
    --- TEXTE BRUT DE QCM À STRUCTURER ---
    {chunk_text}
    """
    
    logger.info(f"Envoi du chunk ({len(chunk_text)} caractères) à l'Agent Agno...")
    
    try:
        response = agent.run(prompt)
        
        # Le framework Agno désérialise et valide automatiquement selon response_format (QCMBatchResponse)
        batch_response: QCMBatchResponse = response.content
        
        questions = batch_response.questions
        logger.info(f"Extraction réussie de {len(questions)} questions via Agno.")
        
        # Injecter post-traitement les valeurs de source_file et category pour garantir la cohérence
        for q in questions:
            q.source_file = source_filename
            q.category = category
            
        return questions
        
    except Exception as e:
        logger.error(f"Erreur lors de la structuration par l'Agent Agno: {e}", exc_info=True)
        # On remonte l'exception pour permettre la reprise ou la mise en œuvre de la stratégie de fallback
        raise e
