import os
import re
import json
import time
import shutil
import tempfile
import requests
import pytesseract
import cv2

from flask import Flask, request, jsonify
from pdf2image import convert_from_path
from threading import Thread

app = Flask(__name__)

# --- IMPORTANT: Configure these paths for your local setup ---
# Path to the 'bin' directory of your Poppler installation
# Example for Windows: POPPLER = r'C:\Program Files\poppler-0.68.0\bin'
# Example for macOS (might vary): POPPLER = r'/opt/homebrew/bin' or r'/usr/local/opt/poppler/bin'
# Example for Linux (often /usr/bin or /usr/local/bin, but check your system):
POPPLER = r'/opt/local/bin' 

# --- Keywords for document grouping ---
TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Tugas", "Surat Keterangan",
                  "Surat Kuasa", "Surat Pelimpahan Wewenang", "Surat Edaran",
                  "Berita Acara", "Nota Dinas", "Keputusan", "Laporan", "Peraturan"]

SALUTATION_KEYWORDS = ["Yth.", "Yang Terhormat", "Kepada"]
REGULATION_KEYWORDS = ["Keputusan tentang", "Peraturan tentang", "No."]

# --- Helper Functions (No changes needed to their core logic) ---
def is_new_document(text):
    """Checks if a page contains keywords that indicate a new document."""
    for keyword in TITLE_KEYWORDS + SALUTATION_KEYWORDS + REGULATION_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
            return True
    return False

def group_pages(ocr_results_dict):
    """Groups OCR results into separate letters based on predefined keywords."""
    grouped_docs = []
    current_doc = ""
    sorted_ocr_items = sorted(ocr_results_dict.items(),
                              key=lambda item: int(re.search(r'\d+', item[0]).group()) if re.search(r'\d+', item[0]) else 0)

    for img_path, text in sorted_ocr_items:
        if current_doc and is_new_document(text):
            grouped_docs.append(current_doc)
            current_doc = text
        else:
            current_doc += "\n" + text if current_doc else text
    if current_doc:
        grouped_docs.append(current_doc)
    return grouped_docs

def download_pdf(pdf_url, output_dir):
    local_pdf_path = os.path.join(output_dir, 'downloaded.pdf')
    try:
        response = requests.get(pdf_url, stream=True)
        response.raise_for_status()
        with open(local_pdf_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                f.write(chunk)
        return local_pdf_path
    except Exception as e:
        raise Exception(f"Download failed: {e}")

# Modified to return a dictionary of page_path: text
def perform_ocr_and_get_page_texts(image_dir):
    """Performs OCR on images and returns a dictionary of page_path: text."""
    ocr_results_per_page = {}
    for filename in sorted(os.listdir(image_dir), key=lambda f: int(re.search(r'\d+', f)
        .group()) if re.search(r'\d+', f) else 0):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            img_path = os.path.join(image_dir, filename)
            img = cv2.imread(img_path)
            if img is None:
                print(f"Gagal baca gambar: {img_path}")
                continue
            text = pytesseract.image_to_string(img).strip()
            ocr_results_per_page[filename] = text
    print('OCR per page completed.')
    return ocr_results_per_page

def is_ugm_format(ocr_text):
    print('Checking UGM format...')
    return "universitas gadjah mada" in ocr_text[:500].lower() 

def classify_document(ocr_text):
    patterns = {
        "surat_tugas": r"(?i)(surat tugas|yang bertanda tangan.*memberikan tugas kepada)",
        "surat_keterangan": r"(?i)(surat keterangan)",
        "surat_permohonan": r"(?i)(permohonan|sehubungan dengan.*terima kasih)",
    }
    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE | re.DOTALL):
            return category.title().replace("_", " ")
    return "Tidak Diketahui"

def detect_patterns(text, letter_type):
    patterns = {
        "Surat Permohonan": {
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Dengan hormat|Sehubungan dengan).*?terima kasih)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "penerima_surat": r"Yth\.\s*(.*?)\s*Dengan", 
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "Surat Tugas": { 
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Yang bertanda tangan|Yang bertandatangan).*?(?:mestinya|semestinya)\.)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "Surat Keterangan": { 
           "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Yang bertanda tangan|Yang bertandatangan).*?(?:mestinya|semestinya)\.)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "default": {
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        }
    }
    result = {}
    pattern_set = patterns.get(letter_type, patterns["default"])
    for key, regex in pattern_set.items():
        match = re.search(regex, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            if key == "tanggal":
                full_match_text = match.group(2).strip()
                start_pos = match.start(2)
                length = len(full_match_text)
            elif key == "ttd_surat":
                full_match_text = match.group(1).strip()
                start_pos = match.start(1)
                length = len(full_match_text)
            else:
                full_match_text = match.group(1).strip()
                start_pos = match.start(1)
                length = len(full_match_text)
            result[key] = {
                'text': full_match_text,
                'start': start_pos,
                'length': length
            }
    return result

# --- Flask Routes ---

@app.route("/")
def index():
    return "Hello from Flask OCR!"

@app.route("/submit_pdf", methods=["POST"])
def submit_pdf():
    data = request.get_json()
    task_id = data.get("task_id")
    pdf_url = data.get("pdf_url")

    if not pdf_url or not task_id:
        return jsonify({"success": False, 
            "message": "Missing 'pdf_url' or 'task_id'"}), 400

    Thread(target=background_process, args=(pdf_url, task_id)).start()
    return jsonify({"success": True, 
        "message": "Job accepted and processing in background"}), 202

def background_process(pdf_url, task_id):
    print(f'Starting background process for task_id: {task_id}')
    temp_dir = tempfile.mkdtemp()
    try:
        local_pdf_path = download_pdf(pdf_url, temp_dir)
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)

        for i, image in enumerate(images):
            image.save(os.path.join(temp_dir, f"page_{i+1}.png"), "PNG")

        # 1. Perform OCR to get text for each page
        ocr_results_per_page = perform_ocr_and_get_page_texts(temp_dir)
        # 2. Group pages into logical documents using is_new_document and group_pages
        grouped_documents = group_pages(ocr_results_per_page)
        # 3. Process each grouped document
        all_processed_docs = []
        for i, doc_text in enumerate(grouped_documents):
            is_ugm = is_ugm_format(doc_text)
            letter_type = classify_document(doc_text)
            extracted_fields = detect_patterns(doc_text, letter_type) 
            all_processed_docs.append({
                "document_index": i + 1,
                "is_ugm_format": is_ugm,
                "letter_type": letter_type,
                "ocr_text": doc_text,
                "extracted_fields": extracted_fields
            })

        new_path = pdf_url.split('suratMasuk/')[-1] if 'suratMasuk/' in pdf_url else os.path.basename(pdf_url)
        headers = {
            'Content-Type': 'application/json',  
            'Accept': 'application/json'         
        }
        try:
            response = requests.post("http://127.0.0.1:8000/api/hook", json={
                "task_id": task_id,
                "pdf_url": new_path,
                "processed_documents": all_processed_docs # Send info for all found docs
            }, headers=headers)
            print(f'Successfully sent results to Laravel. Status Code: {response.status_code}')
        except Exception as e:
            print(f"Failed to send results to Laravel: {e}")
    except Exception as e:
        print(f"Error in background_process for task_id {task_id}: {e}")
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=True, threaded=True)