import os
import streamlit as st

def render_context_column(q: dict, image_dir: str):
    """
    Renders the left column (scientific context and clinical dossier images).
    """
    st.markdown("<h3 style='color: #60a5fa;'>🔍 Contexte d'Origine & Images</h3>", unsafe_allow_html=True)
    
    # 1. Clinical Dossier Context
    if q.get("case_study") and q["case_study"].get("context_text"):
        st.markdown(f"**Cas Clinique ({q['case_study'].get('case_title', 'Dossier')}) :**")
        st.markdown(f"<div class='info-box'>{q['case_study']['context_text']}</div>", unsafe_allow_html=True)
    elif q.get("context"):
        st.markdown("**Contexte de la Question :**")
        st.markdown(f"<div class='info-box'>{q['context']}</div>", unsafe_allow_html=True)
    else:
        st.info("ℹ️ Aucun énoncé clinique parent ou contexte textuel rattaché.")

    # 2. Question Images
    st.markdown("#### 🖼️ Images de l'Énoncé")
    question_images = q.get("question_images", [])
    if question_images:
        for img_name in question_images:
            img_path = os.path.join(image_dir, img_name)
            if os.path.exists(img_path):
                st.image(img_path, caption=img_name, use_container_width=True)
            else:
                st.error(f"Image introuvable localement : {img_name} (Chemin : {img_path})")
    else:
        st.caption("Aucune image associée à l'énoncé.")

    # 3. Correction Images
    st.markdown("#### 🖼️ Images de la Correction")
    correction = q.get("correction") or {}
    correction_images = correction.get("correction_images", [])
    if correction_images:
        for img_name in correction_images:
            img_path = os.path.join(image_dir, img_name)
            if os.path.exists(img_path):
                st.image(img_path, caption=f"Correction - {img_name}", use_container_width=True)
            else:
                st.error(f"Image de correction introuvable : {img_name} (Chemin : {img_path})")
    else:
        st.caption("Aucune image associée aux explications de correction.")
