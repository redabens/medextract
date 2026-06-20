import re
from core.config import (
    get_logger,
    INLINE_CORR_REP_REGEX,
    INLINE_CORR_FIRST_LINE_REGEX,
    INLINE_CORR_EXACT_REGEX
)

logger = get_logger("pdf_corrections")

def extract_inline_correction(explanation_lines):
    """
    Given a list of explanation lines, attempts to extract the correct answer letters
    and the explanation comment.
    """
    if not explanation_lines:
        return None
        
    cleaned_lines = [line.strip() for line in explanation_lines if line.strip()]
    if not cleaned_lines:
        return None
        
    ans = None
    comment_lines = []
    
    for line in cleaned_lines:
        rep_match = INLINE_CORR_REP_REGEX.match(line)
        if rep_match:
            potential_ans = re.sub(r'[\s,\+]', '', rep_match.group(1)).upper()
            if potential_ans and all(c in 'ABCDE' for c in potential_ans):
                ans = potential_ans
                continue
                
        letters_only = re.sub(r'[\s,\+\.\)-]', '', line)
        if re.match(r'^[A-G]{1,7}$', letters_only) and len(line.strip()) <= 10:
            ans = letters_only.upper()
            continue
            
        comment_lines.append(line)
        
    if ans:
        return {
            "answer_letter": "".join(sorted(list(set(ans)))),
            "comment": "\n".join(comment_lines).strip()
        }
        
    first_line = cleaned_lines[0]
    match = INLINE_CORR_FIRST_LINE_REGEX.match(first_line)
    match_exact = INLINE_CORR_EXACT_REGEX.match(first_line)
    
    if match_exact:
        ans = match_exact.group(1).upper()
        comment = ""
        if len(cleaned_lines) > 1:
            comment = "\n".join(cleaned_lines[1:])
        return {
            "answer_letter": "".join(sorted(list(set(ans)))),
            "comment": comment.strip()
        }
        
    if match:
        ans = match.group(1).upper()
        remaining_text = match.group(2).strip()
        is_valid = (
            re.match(r'^\d+\s+', first_line) or 
            len(ans) > 1 or 
            first_line.endswith(ans) or 
            re.match(rf'^(?:\d+\s+)?{ans}\s+[\d-]', first_line, re.IGNORECASE) or
            first_line.startswith(ans + " ") or 
            first_line.startswith(ans + ".") or 
            first_line.startswith(ans + "-") or 
            first_line.startswith(ans + ":")
        )
        if is_valid:
            comment = remaining_text
            if len(cleaned_lines) > 1:
                comment += "\n" + "\n".join(cleaned_lines[1:])
            return {
                "answer_letter": "".join(sorted(list(set(ans)))),
                "comment": comment.strip()
            }
            
    return {
        "answer_letter": "",
        "comment": "\n".join(cleaned_lines).strip()
    }

def parse_grid_corrections(corrections_raw: list) -> list:
    """
    Parses corrections from raw text block sequentially.
    """
    corrections = []
    corr_line_regex = re.compile(r'^(?:[qQ](?:[uU][eE][sS][tT][iI][oO][nN])?\s*)?(\d+)[\s\.:-]+([A-G]{1,7})(?:\s*[\.:-]\s*|\s+|$)(.*)')
    
    current_comment = []
    current_ans = ""
    current_num = -1
    
    for c_line in corrections_raw:
        m = corr_line_regex.match(c_line)
        if m:
            if current_num != -1:
                corrections.append({
                    "num": current_num,
                    "answer_letter": current_ans,
                    "comment": "\n".join(current_comment).strip()
                })
            current_num = int(m.group(1))
            current_ans = m.group(2)
            current_comment = [m.group(3)]
        else:
            if current_num != -1:
                current_comment.append(c_line)
                
    if current_num != -1:
        corrections.append({
            "num": current_num,
            "answer_letter": current_ans,
            "comment": "\n".join(current_comment).strip()
        })
        
    if not corrections:
        grid_matches = re.findall(r'\b(?:Q)?(\d+)[\s\.:-]+([A-G]{1,7})\b', "\n".join(corrections_raw))
        for g in grid_matches:
            corrections.append({
                "num": int(g[0]),
                "answer_letter": g[1],
                "comment": "Explication non détaillée."
            })
            
    return corrections
