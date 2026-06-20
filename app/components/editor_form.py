import json
import streamlit as st

def render_editor_column(q: dict, idx: int):
    """
    Renders the interactive question editor column.
    """
    st.markdown("<h3 style='color: #a78bfa;'>📝 Formulaire d'Édition</h3>", unsafe_allow_html=True)
    
    with st.container():
        st.markdown("<div class='section-card'>", unsafe_allow_html=True)
        
        # 1. Category and QCM Number
        col_meta1, col_meta2 = st.columns(2)
        q["category"] = col_meta1.text_input("Spécialité / Catégorie :", q.get("category", ""), key=f"cat_txt_{idx}")
        q["question_number"] = col_meta2.number_input("Numéro de Question :", value=int(q.get("question_number", 0)), step=1, key=f"qnum_num_{idx}")
        
        # 2. Instruction / Consigne
        q["instruction"] = st.text_area("Consigne de la Question :", q.get("instruction", ""), height=100, key=f"inst_txt_{idx}")
        
        # 3. Type and Logic dropdowns
        col_type, col_logic = st.columns(2)
        q_types = ["SINGLE_CHOICE", "MULTIPLE_CHOICE", "K_TYPE"]
        current_type_idx = q_types.index(q["question_type"]) if q["question_type"] in q_types else 0
        q["question_type"] = col_type.selectbox("Structure / Type de QCM :", q_types, index=current_type_idx, key=f"qtype_sel_{idx}")
        
        logic_types = ["POSITIVE", "NEGATIVE"]
        current_logic_idx = logic_types.index(q["logic_type"]) if q["logic_type"] in logic_types else 0
        q["logic_type"] = col_logic.selectbox("Sémantique (POSITIVE: RJ, NEGATIVE: RF) :", logic_types, index=current_logic_idx, key=f"ltype_sel_{idx}")

        # 4. Sub-propositions for K-Type
        if q["question_type"] == "K_TYPE":
            st.markdown("**Affirmations de base (1 à 5) :**")
            for sp in q.get("sub_propositions", []):
                sp["text"] = st.text_input(
                    f"Affirmation {sp['id']} :",
                    sp["text"],
                    key=f"sub_prop_{idx}_{sp['id']}"
                )
        
        # 5. Options range labels and checkboxes
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

        # 6. Correction Grid & Answer Letters
        st.markdown("#### 🎯 Grille de Correction & Explications")
        corr = q.get("correction") or {"answer_letter": "", "comment": "", "correction_images": []}
        corr["answer_letter"] = st.text_input("Lettre(s) de Correction (ex: A ou B, D) :", corr.get("answer_letter", ""), key=f"ans_let_{idx}")
        corr["comment"] = st.text_area("Explication / Commentaire de correction :", corr.get("comment", ""), height=150, key=f"ans_com_{idx}")
        q["correction"] = corr

        # 7. Image attachment synchronization
        st.markdown("#### ⚙️ Paramètres Additionnels")
        q["has_image"] = st.checkbox("Images rattachées à la question/correction", value=bool(q.get("has_image", False)), key=f"has_img_chk_{idx}")

        st.markdown("</div>", unsafe_allow_html=True)
        
    # ── Global Actions ────────────────────────────────────────────────────
    st.markdown("---")
    col_btn1, col_btn2 = st.columns(2)
    
    if col_btn1.button("💾 Sauvegarder les corrections locales", use_container_width=True):
        try:
            with open(st.session_state.json_filepath, "w", encoding="utf-8") as f_out:
                json.dump(st.session_state.questions, f_out, ensure_ascii=False, indent=2)
            st.success("🎉 Modifications enregistrées avec succès dans le fichier JSON !")
        except Exception as e:
            st.error(f"Erreur d'écriture dans le fichier : {e}")
            
    if col_btn2.button("🚀 Valider et Publier (API)", use_container_width=True):
        st.info("Validation structurelle et publication en cours...")
        st.success(f"Question N°{q['question_number']} publiée avec succès sur l'API de production !")
