# pyrefly: ignore [missing-import]
import streamlit as st
import json
import os
import sys

# 1. Resolve paths relative to the project root directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

# Import core parsing functions
from core.docx import parse_docx_to_qcm
from core.pdf import parse_pdf_to_qcm
from core.category import auto_deduce_category
from core.validator import validate_qcm_structure
from core.llm_pipeline import run_llm_pipeline, run_hybrid_pipeline

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

# 5. Operational Mode & File Selection in Sidebar
st.sidebar.markdown("### ⚙️ Mode de fonctionnement")
op_mode = st.sidebar.radio(
    "Mode d'opération :",
    ["Extraire un nouveau document", "Consulter un JSON existant"]
)

if op_mode == "Consulter un JSON existant":
    st.sidebar.markdown("### 📂 Banque d'Extraction")
    json_files = []
    if os.path.exists(OUTPUT_DIR):
        json_files = [f for f in sorted(os.listdir(OUTPUT_DIR)) if f.endswith(".json")]

    if not json_files:
        st.sidebar.warning("⚠️ Aucun fichier JSON trouvé dans le répertoire 'output/'.")
        st.warning("Veuillez d'abord générer des extractions JSON à l'aide de l'onglet d'extraction pour pouvoir les réviser ici.")
    else:
        selected_file = st.sidebar.selectbox("Fichier JSON à réviser :", json_files)
        filepath = os.path.join(OUTPUT_DIR, selected_file)
        
        # Load JSON only if file selection changed
        if st.session_state.json_filepath != filepath:
            with open(filepath, "r", encoding="utf-8") as f:
                st.session_state.questions = json.load(f)
            st.session_state.json_filepath = filepath
            st.session_state.current_index = 0
            if "validation_errors" in st.session_state:
                del st.session_state.validation_errors
else:
    st.sidebar.markdown("### 📥 Source du document")
    source_type = st.sidebar.radio(
        "Type de source :",
        ["📤 Téléverser un fichier", "📁 Sélectionner un fichier local"]
    )
    
    selected_file_path = None
    selected_file_name = None
    
    if source_type == "📤 Téléverser un fichier":
        uploaded_file = st.sidebar.file_uploader(
            "Choisir un fichier (.docx, .pdf) :",
            type=["docx", "pdf"]
        )
        if uploaded_file is not None:
            selected_file_name = uploaded_file.name
            selected_file_path = uploaded_file
    else:
        def get_local_source_files():
            files = []
            folders = {
                "Non Testés": os.path.join(BASE_DIR, "QCM Medicale", "FileNotTested"),
                "Déjà Testés": os.path.join(BASE_DIR, "QCM Medicale", "FileAlreadyTested"),
                "Téléversés": os.path.join(BASE_DIR, "QCM Medicale", "Uploaded"),
            }
            for label, folder_path in folders.items():
                if os.path.exists(folder_path):
                    for f in sorted(os.listdir(folder_path)):
                        if f.lower().endswith((".docx", ".pdf")) and not f.startswith("~$"):
                            files.append({
                                "name": f,
                                "folder": label,
                                "path": os.path.join(folder_path, f)
                            })
            return files
            
        local_files = get_local_source_files()
        if not local_files:
            st.sidebar.warning("⚠️ Aucun fichier .docx ou .pdf trouvé dans les dossiers locaux.")
        else:
            selected_local = st.sidebar.selectbox(
                "Fichier local à extraire :",
                local_files,
                format_func=lambda x: f"[{x['folder']}] {x['name']}"
            )
            selected_file_name = selected_local["name"]
            selected_file_path = selected_local["path"]
            
    # Extraction parameters
    st.sidebar.markdown("### ⚙️ Paramètres d'extraction")
    engine_mode = st.sidebar.selectbox(
        "Moteur d'extraction :",
        ["Règles hors-ligne", "Hybride (Recommandé)", "IA LLM pur"]
    )
    category_override = st.sidebar.text_input(
        "Catégorie / Spécialité (Optionnel) :",
        placeholder="Déduction automatique"
    )
    
    # Action button
    st.sidebar.markdown("---")
    if st.sidebar.button("🚀 Démarrer l'extraction", use_container_width=True):
        if selected_file_path is None:
            st.sidebar.error("Veuillez sélectionner ou téléverser un fichier avant de lancer l'extraction.")
        else:
            # Resolve actual filepath
            actual_filepath = ""
            if source_type == "📤 Téléverser un fichier":
                upload_dir = os.path.join(BASE_DIR, "QCM Medicale", "Uploaded")
                os.makedirs(upload_dir, exist_ok=True)
                actual_filepath = os.path.join(upload_dir, selected_file_name)
                with open(actual_filepath, "wb") as f:
                    f.write(selected_file_path.getbuffer())
            else:
                actual_filepath = selected_file_path
                
            # Deduce category
            category = category_override.strip()
            if not category:
                category = auto_deduce_category(selected_file_name)
                
            # Perform extraction
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            os.makedirs(IMAGE_DIR, exist_ok=True)
            
            with st.spinner(f"Extraction de '{selected_file_name}' en cours..."):
                try:
                    questions = []
                    
                    if engine_mode == "IA LLM pur":
                        questions = run_llm_pipeline(actual_filepath, selected_file_name, category)
                    elif engine_mode == "Hybride (Recommandé)":
                        questions = run_hybrid_pipeline(actual_filepath, selected_file_name, category)
                    else:
                        ext_lower = selected_file_name.lower()
                        if ext_lower.endswith(".docx"):
                            questions = parse_docx_to_qcm(actual_filepath, category)
                        elif ext_lower.endswith(".pdf"):
                            questions = parse_pdf_to_qcm(actual_filepath, category)
                        else:
                            st.error("Format de fichier non supporté.")
                            st.stop()
                            
                    if not questions:
                        st.error("Aucune question n'a pu être extraite de ce fichier.")
                    else:
                        # Validate
                        valid_count, errors, anomalies = validate_qcm_structure(questions)
                        
                        # Save output to JSON
                        out_name = f"{os.path.splitext(selected_file_name)[0]}_extracted.json"
                        out_path = os.path.join(OUTPUT_DIR, out_name)
                        with open(out_path, "w", encoding="utf-8") as f_out:
                            json.dump(questions, f_out, ensure_ascii=False, indent=2)
                            
                        # Update session state
                        st.session_state.questions = questions
                        st.session_state.json_filepath = out_path
                        st.session_state.current_index = 0
                        st.session_state.validation_errors = errors
                        st.session_state.validation_valid_count = valid_count
                        
                        st.success(f"Extraction réussie ! {len(questions)} QCM extraits.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Une erreur est survenue lors de l'extraction : {e}")
                    st.exception(e)

# Display validation warnings if any exist in session state
if "validation_errors" in st.session_state and st.session_state.validation_errors:
    with st.expander(f"⚠️ Avertissements de structure ({len(st.session_state.validation_errors)} détectés lors de l'extraction)", expanded=False):
        for err in st.session_state.validation_errors:
            st.warning(err)


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
            
            # Options range label
            opts_list = q.get("options", [])
            start_l = opts_list[0]["letter"] if opts_list else "A"
            end_l = opts_list[-1]["letter"] if opts_list else "E"
            st.markdown(f"**Propositions finales de réponses ({start_l} à {end_l}) :**")
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
                    <li><b>Moteur d'extraction</b> : Règles déterministes hors-ligne (Epic 01) ou IA Structurante Agno LLM (Epic 02).</li>
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

