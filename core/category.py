"""
Category Deduction Logic
========================
Helper module to auto-deduce medical specialty category from filenames.
"""

def auto_deduce_category(filename: str) -> str:
    """
    Auto-deduces the medical specialty from the file name.
    Falls back to 'Médecine Générale' if nothing matches.
    """
    name_lower = filename.lower()
    if "cardio" in name_lower:
        return "Cardiologie"
    elif "hge" in name_lower:
        return "Hépato-Gastro-Entérologie"
    elif any(k in name_lower for k in ("diab", "ex 01", "ex 02", "cas clinique 0")):
        return "Diabétologie"
    elif "residanat" in name_lower:
        return "Résidanat"
    elif "pneumo" in name_lower:
        return "Pneumologie"
    elif "nephro" in name_lower:
        return "Néphrologie"
    elif "neuro" in name_lower:
        return "Neurologie"
    elif "dermato" in name_lower:
        return "Dermatologie"
    elif "gyneco" in name_lower or "obstétri" in name_lower:
        return "Gynécologie-Obstétrique"
    elif "pediatr" in name_lower or "pédiatr" in name_lower:
        return "Pédiatrie"
    elif "ortho" in name_lower or "traumato" in name_lower:
        return "Orthopédie-Traumatologie"
    return "Médecine Générale"
