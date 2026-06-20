import re
from core.config import get_logger

logger = get_logger("pdf_preprocessor")

def normalize_pdf_lines(lines: list) -> list:
    """
    Cleans and normalizes lines extracted from a PDF:
    1. Moves question numbers from end of line to front.
    2. Merges split question numbers (split by layout/column artifacts).
    3. Merges table-split option letters.
    """
    # 1. Preprocess: move question numbers from the end of the line to the beginning
    normalized_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m_end = re.match(r'^(.*?)\s+([\.\)-])\s*(\d+)\s*$', line)
        if m_end:
            prefix_text = m_end.group(1).strip()
            q_num = m_end.group(3)
            if int(q_num) < 200 and prefix_text and not re.match(r'^[A-G]$', prefix_text, re.IGNORECASE) and not prefix_text.isdigit():
                normalized_lines.append(f"{q_num}. {prefix_text}")
                continue
        normalized_lines.append(line)
    lines = normalized_lines
    
    # 2. Preprocess: merge split question/correction numbers
    i = 0
    merged_lines = []
    while i < len(lines):
        if (i + 3 < len(lines) and 
            lines[i].isdigit() and len(lines[i]) == 1 and
            lines[i+1].isdigit() and len(lines[i+1]) == 1 and
            lines[i+2] == "." and
            (len(lines[i+3]) >= 1 and (lines[i+3][0].isupper() or lines[i+3].startswith("-")))):
            
            num = lines[i] + lines[i+1]
            opt_text = lines[i+3]
            merged_lines.append(f"{num}. {opt_text}")
            i += 4
            
        elif (i + 2 < len(lines) and 
              lines[i].isdigit() and len(lines[i]) == 1 and
              lines[i+1] == "." and
              (len(lines[i+2]) >= 1 and (lines[i+2][0].isupper() or lines[i+2].startswith("-")))):
              
            num = lines[i]
            opt_text = lines[i+2]
            merged_lines.append(f"{num}. {opt_text}")
            i += 3
            
        else:
            merged_lines.append(lines[i])
            i += 1
            
    lines = merged_lines

    # 3. Preprocess: merge table-split correction numbers and options
    i = 0
    merged_lines = []
    while i < len(lines):
        if (i + 1 < len(lines) and 
            re.match(r'^\d+[\.\)-]?$', lines[i]) and 
            re.match(r'^[A-G]\b', lines[i+1])):
            
            num = re.sub(r'[\dots\.\)-]', '', lines[i])
            merged_lines.append(f"{num}- {lines[i+1]}")
            i += 2
        else:
            merged_lines.append(lines[i])
            i += 1
            
    return merged_lines
