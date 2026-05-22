import re
import hashlib
import os
from core.config import NEG_INDICATORS, OPTION_PARSE_PATTERN

def clean_text(text):
    """
    Cleans and normalizes UTF-8 text, removing non-breaking spaces,
    normalizing curly/slanted quotes, and stripping extraneous spacing.
    """
    if not text:
        return ""
    # Replace non-breaking spaces
    text = text.replace('\xa0', ' ').replace('\u202f', ' ')
    # Normalize curved/slanted apostrophes and quotation marks
    text = text.replace('\u2019', "'").replace('\u2018', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    return text.strip()

def generate_file_hash(filepath):
    """
    Generates a short, stable 8-character MD5 hash based on the file basename.
    """
    basename = os.path.basename(filepath)
    return hashlib.md5(basename.encode('utf-8')).hexdigest()[:8]

def extract_logic_type(text):
    """
    Analyzes the wording of a question instruction to determine if the logic
    is POSITIVE (seeking correct answers / Réponse Juste) or NEGATIVE 
    (seeking incorrect answers / Réponse Fausse).
    """
    text_lower = text.lower()
    for indicator in NEG_INDICATORS:
        if indicator in text_lower:
            return "NEGATIVE"
    return "POSITIVE"

def parse_options_line(text):
    """
    Parses options from a line, supporting both single options and multiple inline options.
    Matches patterns like 'A. text', 'A- text', 'A) text' or 'A (text)' or 'A text'.
    """
    options = []
    # Match A-E preceded by start of string or whitespace, followed by standard separator (. - = ) : ) or space/lookahead for parenthesis/digits
    # Lookahead ensures we don't consume the next option key
    matches = list(re.finditer(OPTION_PARSE_PATTERN, text))
    
    # Verify that the matches are indeed sequential option letters if there are multiple matches
    if len(matches) > 1:
        letters = [m.group(1) for m in matches]
        # Allow any sequential subset of A-E, e.g. A, B, C or B, C, D
        is_sequential = all(ord(letters[i]) + 1 == ord(letters[i+1]) for i in range(len(letters)-1))
        if not is_sequential and letters[0] != 'A':
            return []
            
    for m in matches:
        letter = m.group(1)
        opt_text = clean_text(m.group(2))
        options.append({
            "letter": letter,
            "text": opt_text,
            "is_correct": False
        })
    return options


