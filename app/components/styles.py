import streamlit as st

def inject_custom_css():
    """Injects custom CSS to style the Streamlit interface premium-dark look."""
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
