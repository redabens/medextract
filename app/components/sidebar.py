import os
import json
import streamlit as st

# Core imports
from core.docx import parse_docx_to_qcm
from core.pdf import parse_pdf_to_qcm
from core.category import auto_deduce_category
from core.validator import validate_qcm_structure
from core.hybrid_rules_pipeline import run_hybrid_rules_pipeline
from core.hybrid_llm_pipeline import run_hybrid_llm_pipeline

def get_local_source_files(base_dir):
    files = []
    folders = {
        "Non Testés": os.path.join(base_dir, "QCM Medicale", "FileNotTested"),
        "Déjà Testés": os.path.join(base_dir, "QCM Medicale", "FileAlreadyTested"),
        "Téléversés": os.path.join(base_dir, "QCM Medicale", "Uploaded"),
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

def render_sidebar(base_dir, output_dir, image_dir):
    """
    Renders sidebar navigation, extraction forms, and configuration.
    Updates st.session_state globally.
    """
    # Initialize operational state
    st.sidebar.markdown("### ⚙️ Mode de fonctionnement")
    op_mode = st.sidebar.radio(
        "Mode d'opération :",
        ["Extraire un nouveau document", "Consulter un JSON existant"]
    )
    
    if op_mode == "Consulter un JSON existant":
        st.sidebar.markdown("### 📂 Banque d'Extraction")
        json_files = []
        if os.path.exists(output_dir):
            json_files = [f for f in sorted(os.listdir(output_dir)) if f.endswith(".json")]

        if not json_files:
            st.sidebar.warning("⚠️ Aucun fichier JSON trouvé dans le répertoire 'output/'.")
            st.warning("Veuillez d'abord générer des extractions JSON à l'aide de l'onglet d'extraction pour pouvoir les réviser ici.")
        else:
            selected_file = st.sidebar.selectbox("Fichier JSON à réviser :", json_files)
            filepath = os.path.join(output_dir, selected_file)
            
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
            local_files = get_local_source_files(base_dir)
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
            [
                "Hybride Coopératif (Rules-First) [Par défaut]",
                "Hybride Auto-Adaptatif (LLM-First) [Robuste]",
                "Parser déterministe (Règles locales)"
            ]
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
                    upload_dir = os.path.join(base_dir, "QCM Medicale", "Uploaded")
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
                os.makedirs(output_dir, exist_ok=True)
                os.makedirs(image_dir, exist_ok=True)
                
                with st.spinner(f"Extraction de '{selected_file_name}' en cours..."):
                    try:
                        questions = []
                        
                        if engine_mode == "Hybride Auto-Adaptatif (LLM-First) [Robuste]":
                            questions = run_hybrid_llm_pipeline(actual_filepath, selected_file_name, category)
                        elif engine_mode == "Hybride Coopératif (Rules-First) [Par défaut]":
                            questions = run_hybrid_rules_pipeline(actual_filepath, selected_file_name, category)
                        else:
                            # Offline pure rules
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
                            out_path = os.path.join(output_dir, out_name)
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

    # Render navigation if questions loaded
    if st.session_state.questions:
        questions = st.session_state.questions
        total_questions = len(questions)
        idx = st.session_state.current_index
        q = questions[idx]
        
        st.sidebar.markdown("### 🧭 Navigation")
        st.sidebar.markdown(f"Question **{idx + 1}** sur **{total_questions}**")
        st.sidebar.progress((idx + 1) / total_questions)
        
        col_prev, col_next = st.sidebar.columns(2)
        if col_prev.button("⬅️ Précédent", disabled=(idx == 0), use_container_width=True):
            st.session_state.current_index -= 1
            st.rerun()
        if col_next.button("Suivant ➡️", disabled=(idx == total_questions - 1), use_container_width=True):
            st.session_state.current_index += 1
            st.rerun()
            
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

        st.sidebar.markdown("---")
        st.sidebar.markdown(f"**Fichier Source** : `{q.get('source_file', 'Inconnu')}`")
        st.sidebar.markdown(f"**Index Original** : Question `{q.get('question_number', '?')}`")
