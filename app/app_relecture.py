# pyrefly: ignore [missing-import]
import streamlit as st
import os
import sys

# 1. Resolve paths relative to the project root directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

# Import UI components
from app.components.styles import inject_custom_css
from app.components.sidebar import render_sidebar
from app.components.context_viewer import render_context_column
from app.components.editor_form import render_editor_column

# 2. Configure the Streamlit page layout and theme
st.set_page_config(
    layout="wide",
    page_title="MedExtract - Human in the Loop",
    page_icon="🧬",
    initial_sidebar_state="expanded"
)

# 3. Inject premium design CSS
inject_custom_css()

st.markdown("<h1 class='main-header'>🧬 MedExtract-API — Relecture Humaine</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #94a3b8; font-size: 1.1rem; margin-bottom: 2rem;'>Interface de validation scientifique et correction de QCM médicaux.</p>", unsafe_allow_html=True)

# 4. Session State Initialization
if "questions" not in st.session_state:
    st.session_state.questions = []
if "current_index" not in st.session_state:
    st.session_state.current_index = 0
if "json_filepath" not in st.session_state:
    st.session_state.json_filepath = ""

# 5. Render Sidebar (handles file upload/selection, extraction parameters & navigation)
render_sidebar(BASE_DIR, OUTPUT_DIR, IMAGE_DIR)

# 6. Display validation warnings if any exist in session state
if "validation_errors" in st.session_state and st.session_state.validation_errors:
    with st.expander(f"⚠️ Avertissements de structure ({len(st.session_state.validation_errors)} détectés lors de l'extraction)", expanded=False):
        for err in st.session_state.validation_errors:
            st.warning(err)

# 7. Render Side-by-Side Main layout if data is loaded
if st.session_state.questions:
    questions = st.session_state.questions
    idx = st.session_state.current_index
    q = questions[idx]
    
    col_left, col_right = st.columns([1, 1.2], gap="large")
    
    with col_left:
        render_context_column(q, IMAGE_DIR)
        
    with col_right:
        render_editor_column(q, idx)
else:
    st.info("💡 Veuillez charger ou extraire un document de QCM médicaux pour commencer la relecture.")
    
    # Premium styled user landing page
    st.markdown("""
    <div class='section-card' style='margin-top: 2rem; border-left: 5px solid #3b82f6;'>
        <h3 style='color: #60a5fa; margin-top: 0;'>Bienvenue sur MedExtract-API Relecture Humaine 🧬</h3>
        <p style='color: #cbd5e1; font-size: 1.1rem; line-height: 1.6;'>
            Cette interface interactive vous permet de charger directement des fichiers de QCM médicaux bruts, d'appliquer les pipelines d'extraction, et de valider les questions de manière semi-automatique.
        </p>
        <h4 style='color: #a78bfa; margin-top: 1.5rem;'>⚙️ Guide de démarrage rapide</h4>
        <ol style='color: #cbd5e1; font-size: 1rem; line-height: 1.8;'>
            <li>Dans la barre latérale, sélectionnez <b>"Extraire un nouveau document"</b> sous <i>Mode de fonctionnement</i>.</li>
            <li>Choisissez votre source de document :
                <ul>
                    <li><b>Téléverser un fichier</b> : Faites glisser un document <b>.docx</b> ou <b>.pdf</b>.</li>
                    <li><b>Sélectionner un fichier local</b> : Choisissez parmi les fichiers déjà présents dans le dossier de données de l'application.</li>
                </ul>
            </li>
            <li>Configurez vos options :
                <ul>
                    <li><b>Moteur d'extraction</b> : Règles locales, Hybride Rules-First ou Hybride LLM-First (auto-adaptatif).</li>
                    <li><b>Catégorie / Spécialité</b> : Laissez vide pour que l'algorithme déduise automatiquement la spécialité depuis le nom du fichier.</li>
                </ul>
            </li>
            <li>Cliquez sur <b>🚀 Démarrer l'extraction</b>. Une fois l'extraction terminée, le QCM et ses images s'affichent automatiquement à l'écran.</li>
        </ol>
        <p style='color: #94a3b8; font-size: 0.95rem; margin-top: 2rem;'>
            💡 <i>Note : Pour consulter ou corriger des résultats déjà extraits, sélectionnez <b>"Consulter un JSON existant"</b> dans le mode de fonctionnement.</i>
        </p>
    </div>
    """, unsafe_allow_html=True)
