import os
import sys
import json
import argparse
from core.config import get_logger, OUTPUT_DIR, IMAGE_DIR
from core.docx_parser import parse_docx_to_qcm
from core.pdf_parser import parse_pdf_to_qcm

logger = get_logger("main_orchestrator")

def auto_deduce_category(filename):
    """
    Auto-deduces the medical specialty category based on the file name.
    """
    name_lower = filename.lower()
    if "cardio" in name_lower:
        return "Cardiologie"
    elif "hge" in name_lower:
        return "Hépato-Gastro-Entérologie"
    elif "diab" in name_lower or "ex 01" in name_lower or "ex 02" in name_lower or "cas clinique 0" in name_lower:
        return "Diabétologie"
    elif "residanat" in name_lower:
        return "Résidanat"
    return "Médecine Générale"

def validate_qcm_structure(questions):
    """
    Performs basic logic and structural validations on extracted questions
    to ensure full compliance with target specifications.
    """
    valid_count = 0
    errors = []
    
    for q_idx, q in enumerate(questions):
        q_num = q.get("question_number", "?")
        src = q.get("source_file", "?")
        
        # 1. Check required fields
        required_fields = ["source_file", "category", "question_number", "question_type", "instruction", "logic_type", "has_image", "question_images", "sub_propositions", "options", "correction"]
        missing_fields = [f for f in required_fields if f not in q]
        if missing_fields:
            errors.append(f"Q{q_num} [{src}]: Champs manquants {missing_fields}")
            continue
            
        # 2. Check options validity
        opts = q.get("options", [])
        if not opts:
            errors.append(f"Q{q_num} [{src}]: Aucune proposition de réponse (options)")
            continue
            
        # 3. Check correction validity
        corr = q.get("correction", {})
        if "answer_letter" not in corr:
            errors.append(f"Q{q_num} [{src}]: Lettre de correction manquante")
            continue
            
        # 4. Check K-Type validity
        if q["question_type"] == "K_TYPE" and not q["sub_propositions"]:
            errors.append(f"Q{q_num} [{src}]: Type K_TYPE mais aucune sous-proposition présente")
            continue
            
        valid_count += 1
        
    return valid_count, errors

def main():
    parser = argparse.ArgumentParser(description="Pipeline MedExtract-API : Extraction de QCM médicaux DOCX/PDF.")
    parser.add_argument("--file", type=str, help="Chemin du fichier unique (.docx ou .pdf) à extraire.")
    parser.add_argument("--dir", type=str, help="Chemin du dossier contenant les fichiers QCM.")
    parser.add_argument("--category", type=str, help="Catégorie médicale forcée (écrase la déduction automatique).")
    
    args = parser.parse_args()
    
    # Default to current workspace input folder if no arguments are provided
    input_dir = args.dir
    input_file = args.file
    
    if not input_file and not input_dir:
        # Fallback to local 'QCM Medicale' workspace directory
        local_qcm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "QCM Medicale")
        if os.path.exists(local_qcm_dir):
            input_dir = local_qcm_dir
            logger.info(f"Aucun argument fourni. Analyse par défaut du dossier : {input_dir}")
        else:
            parser.print_help()
            sys.exit(1)
            
    all_extracted_questions = []
    files_to_process = []
    
    # Collect files
    if input_file:
        files_to_process.append(input_file)
    elif input_dir:
        if not os.path.exists(input_dir):
            logger.error(f"Dossier d'entrée introuvable : {input_dir}")
            sys.exit(1)
        for f in os.listdir(input_dir):
            if f.endswith((".docx", ".pdf")) and not f.startswith("~$"):
                files_to_process.append(os.path.join(input_dir, f))
                
    if not files_to_process:
        logger.warning("Aucun fichier valide (.docx ou .pdf) trouvé.")
        sys.exit(0)
        
    logger.info(f"Début du traitement de {len(files_to_process)} fichier(s)...")
    
    for filepath in files_to_process:
        filename = os.path.basename(filepath)
        category = args.category if args.category else auto_deduce_category(filename)
        
        logger.info(f"Traitement de {filename} [Catégorie : {category}]...")
        
        try:
            if filename.lower().endswith(".docx"):
                questions = parse_docx_to_qcm(filepath, category)
            elif filename.lower().endswith(".pdf"):
                questions = parse_pdf_to_qcm(filepath, category)
            else:
                logger.warning(f"Format non supporté pour {filename}")
                continue
                
            all_extracted_questions.extend(questions)
            logger.info(f"Extraction réussie de {len(questions)} questions de {filename}")
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de {filename}: {e}", exc_info=True)
            
    # Validate final structure
    valid_count, errors = validate_qcm_structure(all_extracted_questions)
    
    # Save output JSON
    output_json_path = os.path.join(OUTPUT_DIR, "extracted_qcm.json")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(all_extracted_questions, f, ensure_ascii=False, indent=2)
        
    logger.info("=========================================")
    logger.info(f"Extraction complétée avec succès !")
    logger.info(f"Total questions extraites : {len(all_extracted_questions)}")
    logger.info(f"Questions validées : {valid_count}")
    logger.info(f"Fichier de sortie : {output_json_path}")
    logger.info(f"Dossier des images : {IMAGE_DIR}")
    
    if errors:
        logger.warning(f"{len(errors)} avertissements de structure détectés lors de la validation :")
        for err in errors[:10]:
            logger.warning(f"  - {err}")
        if len(errors) > 10:
            logger.warning(f"  - ... et {len(errors) - 10} autres erreurs.")
    logger.info("=========================================")

if __name__ == "__main__":
    main()
