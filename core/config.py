import os
import logging
import re
from dotenv import load_dotenv

# Load environmental variables from .env file at the project root
load_dotenv()

# 1. Base Directory Configurations
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join(BASE_DIR, "QCM Medicale")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

# 2. LLM Engine Configurations
USE_LLM = False  # Set to True to enable the Agno LLM Engine
LLM_PROVIDER = "openai"  # "openai", "anthropic", etc.
LLM_MODEL = "gpt-4o"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 3. Ensure Directories Exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

# 3. Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def get_logger(name):
    return logging.getLogger(name)

# 4. Centralized Regex & Matching Patterns (Improve system generalizability)
# These can be customized to match different exam templates/styles.

# Pattern to detect the start of a clinical case (e.g. "Cas clinique N°1 :", "Dossier 3:")
CASE_START_PATTERN = r'^(Cas clinique|Dossier)\s*(N°\d+|\d+)\s*:'
CASE_START_REGEX = re.compile(CASE_START_PATTERN, re.IGNORECASE)

# Pattern to detect the start of a question (e.g. "1. ", "42)", "105-", "3:")
QUESTION_START_PATTERN = r'^(\d+)(?:[\.\)-]|:)(?!\d)\s*(.*)'
QUESTION_START_REGEX = re.compile(QUESTION_START_PATTERN)

# Pattern to detect sub-propositions in K-Type questions (e.g. "1. ", "5-")
SUB_PROP_PATTERN = r'^([1-5])(?:[\.\)-]|:)(?!\d)\s*(.*)'
SUB_PROP_REGEX = re.compile(SUB_PROP_PATTERN)

# Pattern to detect standard option format or single option (e.g. "A. text", "E- text")
OPTION_LOOSE_PATTERN = r'^({letter})(?:\s+|[\.\):=-])\s*(.*)'

# Pattern to parse a full line of options, supporting multiple options on a single line (e.g. "A. 1+2  B. 3  C. 4")
OPTION_PARSE_PATTERN = r'(?:^|\s)([A-E])(?:[\.\):=-]|\s+(?=\()|(?=\()|\s+(?=\d))\s*(.*?)(?=\s+(?:[A-E])(?:[\.\):=-]|\s+(?=\()|(?=\()|\s+(?=\d))|$)'

# Pattern to match correction lines in final grid/explanations (e.g. "27. BD", "Q 13 - A comment")
CORR_LINE_PATTERN = r'^(?:[qQ](?:[uU][eE][sS][tT][iI][oO][nN])?\s*)?(\d+)[\s\.:-]+([A-E]{1,5})(?:\s*[\.:-]\s*|\s+|$)(.*)'
CORR_LINE_REGEX = re.compile(CORR_LINE_PATTERN)

# Patterns for inline explanation parsing
INLINE_CORR_REP_PATTERN = r'^(?:R[eé]ponse|Correction|Corrig[eé])\s*[:\s-]+\s*([A-E\s,\+]+)$'
INLINE_CORR_REP_REGEX = re.compile(INLINE_CORR_REP_PATTERN, re.IGNORECASE)

INLINE_CORR_FIRST_LINE_PATTERN = r'^(?:\d+\s+)?([A-E]{1,5})(?:\s*[\.:+\s-]|\s+)(.*)'
INLINE_CORR_FIRST_LINE_REGEX = re.compile(INLINE_CORR_FIRST_LINE_PATTERN, re.IGNORECASE)

INLINE_CORR_EXACT_PATTERN = r'^(?:\d+\s+)?([A-E]{1,5})\s*$'
INLINE_CORR_EXACT_REGEX = re.compile(INLINE_CORR_EXACT_PATTERN, re.IGNORECASE)

# Negative logic indicator words (French medical terms)
NEG_INDICATORS = [
    "fausse", "fausses", "sauf", "incorrect", "incorrecte", 
    "exclut", "ne fait pas partie", "n'est pas", "indiquer l'intrus", 
    "(rf)", "les rf"
]

