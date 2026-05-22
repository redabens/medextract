from lxml import etree

# 1. Namespaces definition for Microsoft Office XML
MATH_NAMESPACES = {
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
}

def parse_omml_element(omml_node):
    """
    Recursively parses an OMML XML node and translates its mathematical
    components into clean LaTeX-style or readable Unicode strings.
    Handles subscripts, superscripts, combined scripts, fractions, and delimiters.
    """
    if omml_node is None:
        return ""
        
    result = []
    
    # Iterate through child nodes to process structural Math elements
    for child in omml_node.iterchildren():
        tag_local = etree.QName(child.tag).localname
        
        # A. Mathematical Text Run
        if tag_local == 't':
            if child.text:
                result.append(child.text)
                
        # B. Subscripts (e.g. PaO2, HCO3)
        elif tag_local == 'sSub':
            base_nodes = child.xpath('./m:e', namespaces=MATH_NAMESPACES)
            sub_nodes = child.xpath('./m:sub', namespaces=MATH_NAMESPACES)
            
            base_text = "".join([parse_omml_element(b) for b in base_nodes])
            sub_text = "".join([parse_omml_element(s) for s in sub_nodes])
            
            if sub_text:
                result.append(f"{base_text}_{{{sub_text}}}")
            else:
                result.append(base_text)
                
        # C. Superscripts (e.g. 10^9, Na+)
        elif tag_local == 'sSup':
            base_nodes = child.xpath('./m:e', namespaces=MATH_NAMESPACES)
            sup_nodes = child.xpath('./m:sup', namespaces=MATH_NAMESPACES)
            
            base_text = "".join([parse_omml_element(b) for b in base_nodes])
            sup_text = "".join([parse_omml_element(s) for s in sup_nodes])
            
            if sup_text:
                result.append(f"{base_text}^{{{sup_text}}}")
            else:
                result.append(base_text)
                
        # D. Subscripts and Superscripts combined (e.g. isotope or dual indices)
        elif tag_local == 'sSubSup':
            base_nodes = child.xpath('./m:e', namespaces=MATH_NAMESPACES)
            sub_nodes = child.xpath('./m:sub', namespaces=MATH_NAMESPACES)
            sup_nodes = child.xpath('./m:sup', namespaces=MATH_NAMESPACES)
            
            base_text = "".join([parse_omml_element(b) for b in base_nodes])
            sub_text = "".join([parse_omml_element(s) for s in sub_nodes])
            sup_text = "".join([parse_omml_element(s) for s in sup_nodes])
            
            if sub_text and sup_text:
                result.append(f"{base_text}_{{{sub_text}}}^{{{sup_text}}}")
            elif sub_text:
                result.append(f"{base_text}_{{{sub_text}}}")
            elif sup_text:
                result.append(f"{base_text}^{{{sup_text}}}")
            else:
                result.append(base_text)
                
        # E. Fractions (e.g. clearance formula or ratios)
        elif tag_local == 'f':
            num_nodes = child.xpath('./m:num', namespaces=MATH_NAMESPACES)
            den_nodes = child.xpath('./m:den', namespaces=MATH_NAMESPACES)
            
            num_text = "".join([parse_omml_element(n) for n in num_nodes])
            den_text = "".join([parse_omml_element(d) for d in den_nodes])
            
            result.append(f"({num_text}/{den_text})")
            
        # F. Delimiters (brackets, parentheses, absolute values)
        elif tag_local == 'd':
            # m:d typically wraps elements in delimiters.
            # We recursively parse all elements inside and wrap in parenthesis
            del_text = "".join([parse_omml_element(e) for e in child.xpath('./m:e', namespaces=MATH_NAMESPACES)])
            # We can detect bracket parameters, but parenthesis is a safe universal fallback
            result.append(f"({del_text})")
            
        # G. Recursive fallback for any other structure
        else:
            child_text = parse_omml_element(child)
            if child_text:
                result.append(child_text)
                
    return "".join(result)
