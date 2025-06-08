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
POPPLER = r'/opt/local/bin' 

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

def perform_ocr_on_images(image_dir):
    ocr_results = []
    for filename in sorted(os.listdir(image_dir)):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            img_path = os.path.join(image_dir, filename)
            img = cv2.imread(img_path)
            if img is None:
                print(f"Gagal baca gambar: {img_path}")
                continue
            text = pytesseract.image_to_string(img).strip()
            ocr_results.append(text)
    print('ocr')
    return "\n\n".join(ocr_results)

def is_ugm_format(ocr_text):
    print('ygm')
    return "universitas gadjah mada" in ocr_text[:300].lower()

TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Tugas", "Surat Keterangan",
                  "Surat Kuasa", "Surat Pelimpahan Wewenang", "Surat Edaran",
                  "Berita Acara", "Nota Dinas", "Keputusan", "Laporan", "Peraturan"]

SALUTATION_KEYWORDS = ["Yth.", "Yang Terhormat", "Kepada"]
REGULATION_KEYWORDS = ["Keputusan tentang", "Peraturan tentang", "No."]

def is_new_document(text):
    """Checks if a page contains keywords that indicate a new document."""
    for keyword in TITLE_KEYWORDS + SALUTATION_KEYWORDS + REGULATION_KEYWORDS:
        # Use re.search for case-insensitive, whole-word matching
        if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
            return True
    return False

def group_pages(ocr_results):
    """Groups OCR results into separate letters based on predefined keywords.
    
    Args:
        ocr_results (dict): A dictionary where keys are document identifiers
                            (e.g., page numbers, image paths) and values are
                            the OCR'd text for that page.
    
    Returns:
        list: A list of strings, where each string represents a grouped document.
    """
    grouped_docs = []
    current_doc = ""

    # Sort items if the order of processing pages matters, 
    # e.g., by image path if they are sequentially named
    sorted_ocr_items = sorted(ocr_results.items()) 

    for img_path, text in sorted_ocr_items:
        if current_doc and is_new_document(text):
            grouped_docs.append(current_doc)  # Save previous document
            current_doc = text  # Start a new one
        else:
            current_doc += "\n" + text if current_doc else text  # Merge if not new

    if current_doc:
        grouped_docs.append(current_doc)  # Save the last document

    return grouped_docs

def classify_document(ocr_text):
    patterns = {
        # "peraturan": r"(?i)\b(peraturan|nomor.*tahun.*tentang|dengan rahmat tuhan|menimbang :, bahwa|meningkat :|memutuskan|menetapkan : peraturan.*tentang|mulai berlaku pada tanggal ditetapkan)\b",
        # "keputusan": r"(?i)\b(keputusan|tentang|menimbang|meningkat|memutuskan|menetapkan : keputusan.*tentang|mulai berlaku pada tanggal ditetapkan)\b",
        # "salinan": r"(?i)\b(salinan|salinan sesuai dengan aslinya)\b",
        # "sop": r"(?i)\b(nomor pos|nama pos)\b",
        # "surat_edaran": r"(?i)\b(surat edaran)\b",
        # "nota_dinas": r"(?i)\b(nota dinas|dari :.*hal :)\b",
        # "memo": r"(?i)\b(memo|dari :)\b",
        # "surat_undangan": r"(?i)\b(hari.*tanggal|pukul|tempat)\b",
        # "kartu_undangan": r"(?i)\b(susunan acara|hari.*tanggal|pukul|tempat|nama acara)\b",
        "surat_tugas": r"(?i)\b(surat tugas|yang bertanda tangan.*memberikan tugas kepada)\b",
        # "surat_kuasa": r"(?i)\b(surat kuasa|yang bertanda tangan.*memberi kuasa kepada)\b",
        # "surat_pelimpahan_wewenang": r"(?i)\b(surat pelimpahan wewenang|melimpahkan wewenang)\b",
        "surat_keterangan": r"(?i)\b(surat keterangan)\b",
        "surat_pernyataan": r"(?i)\b(surat pernyataan|yang bertanda tangan.*menyatakan bahwa)\b",
        "surat_rekomendasi_beasiswa": r"(?i)\b(surat rekomendasi beasiswa)\b",
        # "pengumuman": r"(?i)\b(pengumuman)\b",
        # "berita_acara": r"(?i)\b(berita acara)\b",
        # "laporan": r"(?i)\b(laporan|pendahuluan|tujuan|kesimpulan|saran)\b",
        # "notula": r"(?i)\b(notula|pemimpin rapat|kegiatan rapat)\b",
        # "telaah_staf": r"(?i)\b(telaah staf)\b",
    }

    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            return category.title().replace("_", " ")
    print('jenis')
    return "Tidak Diketahui"

# def detect_patterns(text, letter_type):
#     patterns = {
#         "Surat Permohonan": {
#             "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
#             "pengirim": r"Dari\s*:\s*([^\n]+)",
#             "tujuan": r"Kepada\s*:\s*([^\n]+)"
#         },
#         "Surat Tugas": {
#             "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
#             "isi_surat": r"(Yang bertanda tangan.*?)mestinya\.",
#             "ttd_surat": r"(Ketua|Dekan|Rektor|Direktur)[\s,]*\s*([\w\s.,-]+)\s*NIP\.?\s*(\d+)",
#             "penerima_surat": r"Kepada Yth\.\s*([\w\s.,-]+)"
#         },
#         "Surat Keterangan": {
#             "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
#             # "pengirim": r"Dari\s*:\s*([^\n]+)",
#             "tujuan": r"Kepada\s*:\s*([^\n]+)",
#             "isi_surat": r"(Yang bertanda tangan.*?)mestinya\.",
#             "ttd_surat": r"(Ketua|Dekan|Rektor|Direktur)[\s,]*\s*([\w\s.,-]+)\s*NIP\.?\s*(\d+)",
#             # "penerima_surat": r"Kepada Yth\.\s*([\w\s.,-]+)"
#         },
#         "default": {
#             "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
#             # "pengirim": r"Asal\s*:\s*([^\n]+)"
#         }
#     }

#     result = {}
#     pattern_set = patterns.get(letter_type, patterns["default"])

#     for key, regex in pattern_set.items():
#         match = re.search(regex, text, re.IGNORECASE | re.DOTALL)
#         # match = re.search(regex, text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
#         if match:
#             result[key] = match.group(1).strip()
#     print('pattern')
#     return result

import re
from collections import OrderedDict

def detect_patterns(text, letter_type):
    patterns = {
        "Surat Permohonan": {
            "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
            "pengirim": r"Dari\s*:\s*([^\n]+)",
            "tujuan": r"Kepada\s*:\s*([^\n]+)"
        },
        "Surat Tugas": {
            "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
            "isi_surat": r"(Yang bertanda tangan.*?)mestinya\.",
            "ttd_surat": r"(Ketua|Dekan|Rektor|Direktur)[\s,]*\s*([\w\s.,-]+)\s*NIP\.?\s*(\d+)",
            # "penerima_surat": r"Kepada Yth\.\s*([\w\s.,-]+)"
        },
        "Surat Keterangan": {
            "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
            "tujuan": r"Kepada\s*:\s*([^\n]+)",
            "isi_surat": r"(Yang bertanda tangan.*?)mestinya\.",
            "ttd_surat": r"(Ketua|Dekan|Rektor|Direktur)[\s,]*\s*([\w\s.,-]+)\s*NIP\.?\s*(\d+)"
        },
        "default": {
            "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b"
        }
    }

    result = {}
    pattern_set = patterns.get(letter_type, patterns["default"])

    for key, regex in pattern_set.items():
        match = re.search(regex, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            full_match = match.group(1).strip()
            start_pos = match.start(1)
            length = len(full_match)

            result[key] = {
                'text': full_match,
                'start': start_pos,
                'length': length
            }

    print('pattern')
    return result

@app.route("/")
def index():
    return "Hello from Flask OCR!"

@app.route("/process_pdf", methods=["POST"])
def process_pdf():
    data = request.get_json()
    pdf_url = data.get("pdf_url")

    if not pdf_url:
        return jsonify({"success": False, "message": "Missing 'pdf_url'"}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        local_pdf_path = download_pdf(pdf_url, temp_dir)
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)

        for i, image in enumerate(images):
            image.save(os.path.join(temp_dir, f"page_{i+1}.png"), "PNG")

        ocr_text = perform_ocr_on_images(temp_dir)

        if not is_ugm_format(ocr_text):
            return jsonify({"success": False, "message": "Bukan format Universitas Gadjah Mada"}), 400

        letter_type = classify_document(ocr_text)
        extracted_fields = detect_patterns(ocr_text, letter_type)

        return jsonify({
            "success": True,
            "is_ugm_format": True,
            "letter_type": letter_type,
            "ocr_text": ocr_text,
            "extracted_fields": extracted_fields
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        shutil.rmtree(temp_dir)

def background_process(pdf_url, id):
    print('mulai')
    temp_dir = tempfile.mkdtemp()
    try:
        local_pdf_path = download_pdf(pdf_url, temp_dir)
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)

        for i, image in enumerate(images):
            image.save(os.path.join(temp_dir, f"page_{i+1}.png"), "PNG")

        ocr_text = perform_ocr_on_images(temp_dir)
        is_ugm = is_ugm_format(ocr_text)
        letter_type = classify_document(ocr_text)
        extracted_fields = detect_patterns(ocr_text, letter_type)

        new_path = pdf_url.split('suratMasuk/')[1]

        # Kirim hasil ke Laravel
        headers = {
            'Content-Type': 'application/json',  
            'Accept': 'application/json'         
        }
        try:
            response = requests.post("http://127.0.0.1:8000/api/hook", json={
                "task_id": id,
                "pdf_url": new_path,
                "is_ugm_format": is_ugm,
                "letter_type": letter_type,
                "ocr_text": ocr_text,
                "extracted_fields": extracted_fields
            }, headers=headers)
        
            print('berhasil', response.status_code)
        except Exception as e:
            print(f"Gagal kirim ke Laravel: {e}")
    finally:
        shutil.rmtree(temp_dir)

@app.route("/submit_pdf", methods=["POST"])
def submit_pdf():
    data = request.get_json()
    id = data.get("task_id")
    pdf_url = data.get("pdf_url")

    if not pdf_url:
        return jsonify({"success": False, "message": "Missing 'pdf_url'"}), 400

    Thread(target=background_process, args=(pdf_url, id)).start()
    return jsonify({"success": True, "message": "Job accepted and processing"}), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, threaded=True)
