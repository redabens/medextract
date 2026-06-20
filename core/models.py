from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class SubProposition(BaseModel):
    id: int = Field(description="Identifiant numerique de l'affirmation (ex: 1, 2, 3, 4).")
    text: str = Field(description="Contenu scientifique de l'affirmation.")
    is_true: Optional[bool] = Field(
        default=None,
        description="Si deductible, indique si cette sous-proposition specifique est scientifiquement vraie ou fausse."
    )

class Option(BaseModel):
    letter: Literal["A", "B", "C", "D", "E", "F", "G"] = Field(description="Lettre de l'option (A, B, C, D, E, F ou G).")
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
        description="Les propositions finales de reponses (A-G) proposees a l'etudiant (il peut y en avoir 4, 5, 6, etc.)."
    )
    correction: Correction = Field(description="Donnees de correction et explications.")

class QCMBatchResponse(BaseModel):
    """Conteneur pour forcer l'Agent Agno a retourner une liste d'objets MedExtractQuestion."""
    questions: List[MedExtractQuestion]
