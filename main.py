import os, re, json, random
import cv2
import tempfile
import pytesseract 

from pdf2image import convert_from_path
from flask import Flask, request, jsonify

app = Flask(__name__)


def pdf_to_images(pdf_path):
    """
    Converts a PDF file to a series of images.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to save the generated images.
    """
    temp_dir = tempfile.mkdtemp()

    try:
        images = convert_from_path(pdf_path)
        for i, image in enumerate(images):
            image_path = os.path.join(temp_dir, f"page_{i+1}.png")
            image.save(image_path, "PNG")
        print(f"Successfully converted PDF to images in {temp_dir}")
        return temp_dir
    except Exception as e:
        print(f"An error occurred: {e}")

def perform_ocr_on_images(temp_dir):
    """Performs OCR on images in the given temporary directory."""
    ocr_results = {}
    
    for filename in sorted(os.listdir(temp_dir)):  # Sort files in order
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            img_path = os.path.join(temp_dir, filename)

            img = cv2.imread(img_path)
            if img is None:
                print(f"Error reading image: {img_path}")
                continue

            # gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            text = pytesseract.image_to_string(img).strip()
            ocr_results[img_path] = text  # Store OCR result

    return group_pages(ocr_results)

def is_new_document(text, ocr_result):
    """Checks if a page contains keywords that indicate a new document and return json response."""
    TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Tugas", "Surat Keterangan",
                  "Surat Kuasa", "Surat Pelimpahan Wewenang", "Surat Edaran",
                  "Berita Acara", "Nota Dinas", "Keputusan", "Laporan", "Peraturan"]
    


    SALUTATION_KEYWORDS = ["Yth.", "Yang Terhormat", "Kepada"]
    REGULATION_KEYWORDS = ["Keputusan tentang", "Peraturan tentang", "No."]
    for keyword in TITLE_KEYWORDS + SALUTATION_KEYWORDS + REGULATION_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
            return {'text': text, 'result': True}
        
    return {'text': text, 'result': False}

def group_pages(ocr_results):
    """Groups OCR results into separate letters based on predefined keywords."""
    grouped_docs = []
    current_doc = ""
    
    for img_path, text in ocr_results.items():        
        is_new = is_new_document(text,ocr_results)
        if current_doc and is_new['result']:
            grouped_docs.append(current_doc)  # Save previous document
            current_doc = text  # Start a new one
        else:
            current_doc += "\n" + text if current_doc else text  # Merge if not new

    if current_doc:
        grouped_docs.append(current_doc)  # Save last document
    
    return grouped_docs  # Returns a list of grouped letters

def classify_document(ocr_text):
    """Classifies a document using regex patterns only."""
    patterns = {
        "peraturan": r"(?i)\b(Peraturan|NOMOR\s+.+?\s+TAHUN\s+.+?|TENTANG|MEMUTUSKAN|Menetapkan\s*:\s*PERATURAN\s+.+?\s+TENTANG)\b",
        "keputusan": r"(?i)\b(Keputusan|MEMUTUSKAN|Menetapkan\s*:\s*KEPUTUSAN\s+.+?\s+TENTANG)\b",
        "surat_edaran": r"(?i)\b(Surat Edaran)\b",
        "nota_dinas": r"(?i)\b(NOTA DINAS|Dari\s*:\s*.+?|Hal\s*:\s*.+?)\b",
        "memo": r"(?i)\b(MEMO|Dari\s*:\s*.+?)\b",
        "surat_tugas": r"(?i)\b(SURAT TUGAS|memberikan tugas kepada)\b",
        "surat_kuasa": r"(?i)\b(SURAT KUASA|memberi kuasa kepada)\b",
        "surat_keterangan": r"(?i)\b(SURAT KETERANGAN)\b",
        "surat_pernyataan": r"(?i)\b(SURAT PERNYATAAN|menyatakan bahwa)\b",
        "surat_permohonan": r"(?i)\b(Permohonan|mengajukan permohonan)\b",
        "berita_acara": r"(?i)\b(BERITA ACARA)\b",
        "laporan": r"(?i)\b(LAPORAN|Pendahuluan|Tujuan|Kesimpulan)\b"
    }

    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text):
            return category

    return "Tidak Diketahui"

def is_ugm_format(ocr_text):
    """Checks if the text contains 'universitas gadjah mada' in the first section."""
    first_section = ocr_text[:300].lower()  # First 300 characters, case-insensitive
    if "universitas gadjah mada" in first_section:
        return True
    else:
        return False

def classify_letters(letters):
    """Classifies each letter using regex-based rules only."""
    classified_results = []

    for i, letter_text in enumerate(letters):
        letter_type = classify_document(letter_text)  # Classify using regex
        classified_results.append(letter_type)

    return classified_results

def detect_patterns(text, type):
    """
    Detects specific patterns within a text based on the given letter type.
    
    Args:
        text (str): The input text to search for patterns.
        type (str): The type of letter to determine which patterns to use.
    
    Returns:
        list: A list of dictionaries, each containing information about a matched pattern.
              Returns an empty list if no matches are found or if the type is not recognized.
    """
    patterns = {
        "peraturan": {
            "description": "Mencari nomor, tahun, dan pasal",
            "patterns": [
                {"key": "nomor", "regex": r"(?i)NOMOR\s*:\s*([^\s]+)"},
                {"key": "tahun", "regex": r"(?i)TAHUN\s*:\s*([^\s]+)"},
                {"key": "pasal", "regex": r"(?i)Pasal\s*([^\s.]+)"},
            ],
        },
        "keputusan": {
            "description": "Mencari nomor, tahun",
            "patterns": [
                {"key": "nomor", "regex": r"(?i)NOMOR\s*:\s*([^\s]+)"},
                {"key": "tahun", "regex": r"(?i)TAHUN\s*:\s*([^\s]+)"},
            ],
        },
        "surat_tugas": {
            "description": "Mencari nomor, nama yang bertugas, dan tanggal pelaksanaan",
            "patterns": [
                {"key": "nomor", "regex": r"(?i)NOMOR\s*:\s*([^\s]+)"},
                {"key": "nama", "regex": r"(?i)Nama\s*:\s*([^\n]+)"},
                {"key": "tanggal", "regex": r"(?i)Tanggal\s*:\s*([^\s]+)"},
            ],
        },
        "surat_kuasa": {
            "description": "Mencari nomor, nama yang memberi kuasa, dan nama yang menerima kuasa",
            "patterns": [
                {"key": "nomor", "regex": r"(?i)NOMOR\s*:\s*([^\s]+)"},
                {"key": "pemberi_kuasa", "regex": r"(?i)Nama\s*:\s*([^\n]+)"},
                {"key": "penerima_kuasa", "regex": r"(?i)Nama\s*:\s*([^\n]+)"},
            ],
        },
        "surat_pernyataan": {
            "description": "Mencari nama",
            "patterns": [
                {"key": "nama", "regex": r"(?i)Nama\s*:\s*([^\n]+)"},
            ],
        },
        "surat_keterangan":{
            "description": "Mencari nama",
             "patterns": [
                {"key": "nama", "regex": r"(?i)Nama\s*:\s*([^\n]+)"},
            ],
        },
        "default": {
            "description": "No specific patterns defined for this type.",
            "patterns": [],
        },
    }
    
    letter_info = patterns.get(type, patterns["default"])  # If type doesn't exist, use the default
    results = []
    
    if letter_info["patterns"]:
        for pattern in letter_info["patterns"]:
            matches = re.findall(pattern["regex"], text)
            if matches:
                results.extend([{pattern["key"]: match.strip()} for match in matches])

    return results


@app.route("/")
def hello_world():
    """Example Hello World route."""
    name = os.environ.get("NAME", "World")
    return f"Hello {name}!"

@app.route('/pdf_to_images', methods=['POST'])
def pdf_to_images_endpoint():
    data = request.get_json()
    pdf_path = data.get('pdf_path')
    temp_dir = pdf_to_images(pdf_path)
    return jsonify({'temp_dir': temp_dir})


@app.route('/is_ugm_format', methods=['POST'])
def is_ugm_format_endpoint():
    data = request.get_json()
    ocr_text = data.get('ocr_text')
    result = is_ugm_format(ocr_text)
    return jsonify({'result': result})

@app.route('/classify_letters', methods=['POST'])
def classify_letters_endpoint():
    data = request.get_json()
    letters = data.get('letters')
    result = classify_letters(letters)
    return jsonify(result)

@app.route('/detect_patterns', methods=['POST'])
def detect_patterns_endpoint():
    data = request.get_json()
    text = data.get('text')
    type = data.get('type')
    result = detect_patterns(text,type)
    return jsonify(result)

@app.route('/process_pdf', methods=['POST'])
def process_pdf():
    data = request.get_json()
    pdf_path = data.get('pdf_path')

    temp_dir = pdf_to_images(pdf_path)
    grouped_ocr_result = perform_ocr_on_images(temp_dir)

    is_ugm = False
    if grouped_ocr_result:
      is_ugm = is_ugm_format(grouped_ocr_result[0])
    
    # return jsonify({"is_ugm_format": True, "grouped_ocr_result": grouped_ocr_result})
    return jsonify({"is_ugm_format": is_ugm, "grouped_ocr_result": grouped_ocr_result})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))