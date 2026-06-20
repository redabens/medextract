import re
from core.config import get_logger
from core.docx.extractor import NAMESPACES, parse_cell_node

logger = get_logger("docx_helpers")

# Explicit exam annotation detector (unnumbered questions helper)
UNNUMBERED_Q_ANNOTATION = re.compile(
    r'\(?\d{4}\s+P\d+-\d+T\)?\s*$|'           # Exam source annotation: (2023 P8-1T)
    r'\(cochez\s+la\s+r[eé]ponse|'             # Explicit "(cochez la réponse..."
    r'cocher\s+la\s+r[eé]ponse|'               # Without parenthesis "cocher la réponse"
    r'\(indiquez|'                              # "(indiquez..."
    r'\(parmi\s+les',                           # "(parmi les..."
    re.IGNORECASE
)

def parse_table_element(table_elem, filename):
    """
    Parses a single correction table element and returns list of correction dictionaries.
    """
    if table_elem.xpath('./ancestor::w:tc', namespaces=NAMESPACES):
        return []

    rows = table_elem.xpath('./w:tr', namespaces=NAMESPACES)
    if not rows:
        return []

    first_row_cells = rows[0].xpath('./w:tc', namespaces=NAMESPACES)
    n_cols = len(first_row_cells)

    if n_cols > 3:
        logger.debug(f"[{filename}] Table ignorée (tableau pédagogique, {n_cols} colonnes).")
        return []

    table_corrs = []

    if n_cols == 1 and len(rows) > 1:
        extra_lines = []
        for row in rows:
            cells = row.xpath('./w:tc', namespaces=NAMESPACES)
            if cells:
                cell_text = parse_cell_node(cells[0]).strip()
                if cell_text:
                    extra_lines.append(cell_text)
        if extra_lines:
            first_line = extra_lines[0].strip()
            ans_match = re.match(r'^([A-G]{1,7})', first_line)
            if ans_match:
                ans = ans_match.group(1).upper()
                comment = "\n".join(extra_lines[1:]) if len(extra_lines) > 1 else ""
                table_corrs.append({"answer_letter": ans, "comment": comment, "q_num": None})
            else:
                comment = "\n".join(extra_lines)
                table_corrs.append({"answer_letter": "", "comment": comment, "q_num": None, "is_append": True})
        return table_corrs

    for r_idx, row in enumerate(rows):
        cells = row.xpath('./w:tc', namespaces=NAMESPACES)
        if not cells:
            continue

        cell_texts = [parse_cell_node(c) for c in cells]

        if len(cell_texts) == 2:
            ans = cell_texts[0].strip()
            if ans.lower() in ("question", "numéro", "num", "réponse", "réponses", "correction"):
                continue

            match = re.match(r'^(?:Q|QST|Question\s*)?(\d+)\s*[\s\.:\)-]+\s*([A-G]{1,7})$', ans, re.IGNORECASE)
            if match:
                q_num = int(match.group(1))
                ans_letter = match.group(2).upper()
                table_corrs.append({
                    "q_num": q_num,
                    "answer_letter": ans_letter,
                    "comment": cell_texts[1]
                })
            else:
                ans_clean = re.sub(r'[\s,+]', '', ans)
                if re.match(r'^[A-Ga-g]{1,7}$', ans_clean):
                    table_corrs.append({
                        "q_num": None,
                        "answer_letter": ans.upper(),
                        "comment": cell_texts[1]
                    })
        elif len(cell_texts) == 3:
            q_col = cell_texts[0].strip()
            ans = cell_texts[1].strip()
            if ans.lower() in ("réponse", "réponses", "correction") or q_col.lower() in ("question", "numéro", "num"):
                continue

            q_num_match = re.search(r'(\d+)', q_col)
            q_num = int(q_num_match.group(1)) if q_num_match else None

            ans_clean = re.sub(r'[\s,+/&\-]', '', ans)
            if re.match(r'^[A-Ga-g]{0,7}$', ans_clean):
                table_corrs.append({
                    "q_num": q_num,
                    "answer_letter": ans.upper(),
                    "comment": cell_texts[2]
                })
    return table_corrs
