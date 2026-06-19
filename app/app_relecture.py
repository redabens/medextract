import streamlit as st
import json
import os

# 1. Resolve paths relative to the project root directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

# 2. Configure the Streamlit page layout and theme
st.set_page_config(
    layout="wide",
    page_title="MedExtract - Human in the Loop",
    page_icon="🧬",
    initial_sidebar_state="expanded"
)

# 3. Inject custom CSS for premium styling (Harmonious colors, clean cards, modern typography)
st.markdown("""
<style>
    /* Premium Styling Updates */
    .stApp {
        background-color: #0f172a;
        color: #f1f5f9;
    }
    .main-header {
        font-family: 'Outfit', 'Inter', sans-serif;
        background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        margin-bottom: 0.5rem;
    }
    .section-card {
        background-color: #1e293b;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #334155;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }
    .info-box {
        background-color: #1e293b;
        border-left: 5px solid #6366f1;
        padding: 1rem;
        border-radius: 4px;
        margin-bottom: 1rem;
    }
    .stButton>button {
        background: linear-gradient(135deg, #4f46e5 0%, #3b82f6 100%);
        color: white;
        border: none;
        padding: 0.5rem 1.5rem;
        font-weight: 600;
        border-radius: 8px;
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 class='main-header'>🧬 MedExtract-API — Relecture Humaine</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #94a3b8; font-size: 1.1rem; margin-bottom: 2rem;'>Interface de validation scientifique et correction de QCM médicaux.</p>", unsafe_allow_html=True)

# 4. Session State Initialization
if "questions" not in st.session_state:
    st.session_state.questions = []
if "current_index" not in st.session_state:
    st.session_state.current_index = 0
if "json_filepath" not in st.session_state:
    st.session_state.json_filepath = ""

# 5. File Selection in Sidebar
st.sidebar.markdown("### 📂 Banque d'Extraction")
json_files = []
if os.path.exists(OUTPUT_DIR):
    json_files = [f for f in sorted(os.listdir(OUTPUT_DIR)) if f.endswith(".json")]

if not json_files:
    st.sidebar.warning("⚠️ Aucun fichier JSON trouvé dans le répertoire 'output/'.")
    st.warning("Veuillez d'abord générer des extractions JSON à l'aide de main.py pour pouvoir les réviser ici.")
else:
    selected_file = st.sidebar.selectbox("Fichier JSON à réviser :", json_files)
    filepath = os.path.join(OUTPUT_DIR, selected_file)
    
    # Load JSON only if file selection changed
    if st.session_state.json_filepath != filepath:
        with open(filepath, "r", encoding="utf-8") as f:
            st.session_state.questions = json.load(f)
        st.session_state.json_filepath = filepath
        st.session_state.current_index = 0

# 6. Render interface if data is loaded
if st.session_state.questions:
    questions = st.session_state.questions
    total_questions = len(questions)
    idx = st.session_state.current_index
    q = questions[idx]
    
    # ── Sidebar Navigation ───────────────────────────────────────────────────
    st.sidebar.markdown("### 🧭 Navigation")
    
    # Step indicator
    st.sidebar.markdown(f"Question **{idx + 1}** sur **{total_questions}**")
    st.sidebar.progress((idx + 1) / total_questions)
    
    col_prev, col_next = st.sidebar.columns(2)
    if col_prev.button("⬅️ Précédent", disabled=(idx == 0), use_container_width=True):
        st.session_state.current_index -= 1
        st.rerun()
    if col_next.button("Suivant ➡️", disabled=(idx == total_questions - 1), use_container_width=True):
        st.session_state.current_index += 1
        st.rerun()
        
    # Jump to question input
    jump_idx = st.sidebar.number_input(
        "Aller à la question :",
        min_value=1,
        max_value=total_questions,
        value=idx + 1,
        step=1
    )
    if jump_idx - 1 != idx:
        st.session_state.current_index = jump_idx - 1
        st.rerun()

    # Source File Badge
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Fichier Source** : `{q.get('source_file', 'Inconnu')}`")
    st.sidebar.markdown(f"**Index Original** : Question `{q.get('question_number', '?')}`")

    # ── Side-by-Side Main Layout ──────────────────────────────────────────────
    col_left, col_right = st.columns([1, 1.2], gap="large")
    
    # ────────────────────────────────────────────────────────────────
    # GAUCHE : Contexte Médical et Images
    # ────────────────────────────────────────────────────────────────
    with col_left:
        st.markdown("<h3 style='color: #60a5fa;'>🔍 Contexte d'Origine & Images</h3>", unsafe_allow_html=True)
        
        # Clinical Dossier Context
        if q.get("case_study") and q["case_study"].get("context_text"):
            st.markdown(f"**Cas Clinique ({q['case_study'].get('case_title', 'Dossier')}) :**")
            st.markdown(f"<div class='info-box'>{q['case_study']['context_text']}</div>", unsafe_allow_html=True)
        elif q.get("context"):
            st.markdown("**Contexte de la Question :**")
            st.markdown(f"<div class='info-box'>{q['context']}</div>", unsafe_allow_html=True)
        else:
            st.info("ℹ️ Aucun énoncé clinique parent ou contexte textuel rattaché.")

        # Question Images
        st.markdown("#### 🖼️ Images de l'Énoncé")
        question_images = q.get("question_images", [])
        if question_images:
            for img_name in question_images:
                img_path = os.path.join(IMAGE_DIR, img_name)
                if os.path.exists(img_path):
                    st.image(img_path, caption=img_name, use_container_width=True)
                else:
                    st.error(f"Image introuvable localement : {img_name} (Chemin : {img_path})")
        else:
            st.caption("Aucune image associée à l'énoncé.")

        # Correction Images
        st.markdown("#### 🖼️ Images de la Correction")
        correction = q.get("correction") or {}
        correction_images = correction.get("correction_images", [])
        if correction_images:
            for img_name in correction_images:
                img_path = os.path.join(IMAGE_DIR, img_name)
                if os.path.exists(img_path):
                    st.image(img_path, caption=f"Correction - {img_name}", use_container_width=True)
                else:
                    st.error(f"Image de correction introuvable : {img_name} (Chemin : {img_path})")
        else:
            st.caption("Aucune image associée aux explications de correction.")

    # ────────────────────────────────────────────────────────────────
    # DROITE : Formulaire Interactif d'Édition du QCM
    # ────────────────────────────────────────────────────────────────
    with col_right:
        st.markdown("<h3 style='color: #a78bfa;'>📝 Formulaire d'Édition</h3>", unsafe_allow_html=True)
        
        with st.container():
            st.markdown("<div class='section-card'>", unsafe_allow_html=True)
            
            # Category and QCM Number
            col_meta1, col_meta2 = st.columns(2)
            q["category"] = col_meta1.text_input("Spécialité / Catégorie :", q.get("category", ""))
            q["question_number"] = col_meta2.number_input("Numéro de Question :", value=int(q.get("question_number", 0)), step=1)
            
            # Instruction / Consigne
            q["instruction"] = st.text_area("Consigne de la Question :", q.get("instruction", ""), height=100)
            
            # Type and Logic dropdowns
            col_type, col_logic = st.columns(2)
            q_types = ["SINGLE_CHOICE", "MULTIPLE_CHOICE", "K_TYPE"]
            current_type_idx = q_types.index(q["question_type"]) if q["question_type"] in q_types else 0
            q["question_type"] = col_type.selectbox("Structure / Type de QCM :", q_types, index=current_type_idx)
            
            logic_types = ["POSITIVE", "NEGATIVE"]
            current_logic_idx = logic_types.index(q["logic_type"]) if q["logic_type"] in logic_types else 0
            q["logic_type"] = col_logic.selectbox("Sémantique (POSITIVE: RJ, NEGATIVE: RF) :", logic_types, index=current_logic_idx)

            # Sub-propositions for K-Type
            if q["question_type"] == "K_TYPE":
                st.markdown("**Affirmations de base (1 à 5) :**")
                for sp in q.get("sub_propositions", []):
                    sp["text"] = st.text_input(
                        f"Affirmation {sp['id']} :",
                        sp["text"],
                        key=f"sub_prop_{idx}_{sp['id']}"
                    )
            
            # Options (A-E)
            st.markdown("**Propositions finales de réponses (A à E) :**")
            for i, opt in enumerate(q.get("options", [])):
                col_txt, col_check = st.columns([6, 1])
                opt["text"] = col_txt.text_input(
                    f"Option {opt['letter']} :",
                    opt["text"],
                    key=f"opt_txt_{idx}_{opt['letter']}"
                )
                opt["is_correct"] = col_check.checkbox(
                    "Vraie",
                    value=bool(opt["is_correct"]),
                    key=f"opt_chk_{idx}_{opt['letter']}"
                )

            # Correction Grid & Answer Letters
            st.markdown("#### 🎯 Grille de Correction & Explications")
            corr = q.get("correction") or {"answer_letter": "", "comment": "", "correction_images": []}
            corr["answer_letter"] = st.text_input("Lettre(s) de Correction (ex: A ou B, D) :", corr.get("answer_letter", ""))
            corr["comment"] = st.text_area("Explication / Commentaire de correction :", corr.get("comment", ""), height=150)
            q["correction"] = corr

            # Image attachment synchronization
            st.markdown("#### ⚙️ Paramètres Additionnels")
            q["has_image"] = st.checkbox("Images rattachées à la question/correction", value=bool(q.get("has_image", False)))

            st.markdown("</div>", unsafe_allow_html=True)
            
        # ── Global Actions ────────────────────────────────────────────────────
        st.markdown("---")
        col_btn1, col_btn2 = st.columns(2)
        
        if col_btn1.button("💾 Sauvegarder les corrections locales", use_container_width=True):
            # Save the updated questions state back to the loaded JSON file
            try:
                with open(st.session_state.json_filepath, "w", encoding="utf-8") as f_out:
                    json.dump(st.session_state.questions, f_out, ensure_ascii=False, indent=2)
                st.success("🎉 Modifications enregistrées avec succès dans le fichier JSON !")
            except Exception as e:
                st.error(f"Erreur d'écriture dans le fichier : {e}")
                
        if col_btn2.button("🚀 Valider et Publier (API)", use_container_width=True):
            # Mock publisher action (will connect to Epic 04 HTTP client later)
            st.info("Validation structurelle et publication en cours...")
            st.success(f"Question N°{q['question_number']} publiée avec succès sur l'API de production !")
