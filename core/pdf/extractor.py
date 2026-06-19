import os
import fitz  # PyMuPDF
from core.config import get_logger, IMAGE_DIR
from core.utils import generate_file_hash

logger = get_logger("pdf_extractor")

def extract_pdf_media_and_text(pdf_path):
    """
    Parses a PDF file page by page using PyMuPDF.
    Extracts high-resolution images, determines their bounding boxes,
    and inserts placeholders [[IMG_ID]] into the closest text block.
    Returns a unified text representing the document.
    """
    if not os.path.exists(pdf_path):
        logger.error(f"Fichier PDF introuvable: {pdf_path}")
        return ""
        
    doc = fitz.open(pdf_path)
    file_hash = generate_file_hash(pdf_path)
    
    extracted_pages = []
    
    for page_idx, page in enumerate(doc):
        # 1. Retrieve all text blocks with their spatial coordinates (bbox)
        # Block tuple: (x0, y0, x1, y1, "text", block_no, block_type)
        text_blocks = page.get_text("blocks")
        
        # Convert to list of dicts for simpler manipulation
        blocks = []
        for b in text_blocks:
            blocks.append({
                "bbox": fitz.Rect(b[0], b[1], b[2], b[3]),
                "text": b[4],
                "type": b[6]  # 0 = text, 1 = image
            })
            
        # 2. Retrieve all images located on the current page
        image_list = page.get_images(full=True)
        
        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            img_rects = page.get_image_rects(xref)
            if not img_rects:
                continue
                
            img_bbox = img_rects[0]  # Take primary coordinates
            
            # Extract raw image binary data
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
            except Exception as e:
                logger.warning(f"Impossible d'extraire l'image xref {xref} p. {page_idx+1}: {e}")
                continue
                
            # Create a unique normalized filename and save it
            unique_img_id = f"IMG_{file_hash}_P{page_idx+1}_I{img_idx+1}"
            img_filename = f"{unique_img_id}.{image_ext}"
            img_dest = os.path.join(IMAGE_DIR, img_filename)
            
            with open(img_dest, "wb") as f_img:
                f_img.write(image_bytes)
                
            # 3. Spatial Anchoring: find the text block right below the image
            best_block_idx = -1
            min_distance = float('inf')
            
            for idx, block in enumerate(blocks):
                if block["type"] == 0:  # Text block
                    # Compute vertical distance from the bottom of the image (y1) to the top of the text (y0)
                    dist_y = block["bbox"].y0 - img_bbox.y1
                    # Check if the block is below and closer than previous matches
                    if 0 <= dist_y < min_distance:
                        min_distance = dist_y
                        best_block_idx = idx
                        
            # If found, inject the placeholder at the beginning of the text block
            if best_block_idx != -1:
                blocks[best_block_idx]["text"] = f"[[{unique_img_id}]]\n" + blocks[best_block_idx]["text"]
            else:
                # Fallback: create a virtual text block at the image coordinate
                blocks.append({
                    "bbox": img_bbox,
                    "text": f"\n[[{unique_img_id}]]\n",
                    "type": 0
                })
                
        # Re-sort all blocks vertically (y0) then horizontally (x0) to maintain normal reading order
        blocks.sort(key=lambda b: (b["bbox"].y0, b["bbox"].x0))
        
        # Concat page text
        page_text = "\n".join([b["text"].strip() for b in blocks if b["text"].strip()])
        extracted_pages.append(page_text)
        
    return "\n\n--- PAGE_SEPARATOR ---\n\n".join(extracted_pages)

def extract_pdf_raw_paragraphs(pdf_path: str) -> list:
    """
    Extracts a flat list of raw text paragraphs from a PDF file for use by
    the LLM pipeline (Epic 02). Image extraction and [[IMG_...]] placeholder
    injection are handled by the existing extract_pdf_media_and_text() function.
    """
    filename = os.path.basename(pdf_path)

    # Reuse the existing physical extraction (images + spatial anchoring)
    raw_full_text = extract_pdf_media_and_text(pdf_path)

    if not raw_full_text:
        return []

    paragraphs_out = []
    for line in raw_full_text.split('\n'):
        line = line.strip()
        # Skip internal page separator markers
        if not line or line == "--- PAGE_SEPARATOR ---":
            continue
        paragraphs_out.append(line)

    logger.info(f"[{filename}] {len(paragraphs_out)} paragraphes extraits pour le pipeline LLM.")
    return paragraphs_out
