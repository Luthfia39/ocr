import os
import re
import json
import requests
import pytesseract
from pdf2image import convert_from_path
import cv2
from flask import Flask, request, jsonify, send_file
import tempfile
import shutil
import time

app = Flask(__name__)


def download_pdf(pdf_url, output_dir):
    """Download PDF dari URL ke folder sementara"""
    local_pdf_path = os.path.join(output_dir, 'downloaded.pdf')
    
    try:
        response = requests.get(pdf_url, stream=True)
        response.raise_for_status()
        
        with open(local_pdf_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    
        return local_pdf_path
    
    except Exception as e:
        raise Exception(f"Download failed: {e}")


def perform_ocr_on_images(temp_dir):
    """Convert tiap gambar ke teks dan gabung semua halaman"""
    ocr_results = []

    for filename in sorted(os.listdir(temp_dir)):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            img_path = os.path.join(temp_dir, filename)
            img = cv2.imread(img_path)
            
            if img is None:
                print(f"Gagal baca gambar: {img_path}")
                continue
                
            text = pytesseract.image_to_string(img).strip()
            ocr_results.append(text)

    full_ocr_text = "\n\n".join(ocr_results)
    return full_ocr_text


def group_pages(ocr_results):
    """Deteksi apakah ada lebih dari 1 surat dalam 1 PDF"""
    TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Kuasa", "Surat Tugas", 
                      "Berita Acara", "Nota Dinas", "Permohonan"]

    current_doc = ""
    docs = []

    for text in ocr_results:
        if any(re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE) for kw in TITLE_KEYWORDS):
            if current_doc:
                docs.append(current_doc)
                current_doc = ""
        current_doc += "\n\n" + text

    if current_doc:
        docs.append(current_doc)

    return docs

def is_ugm_format(ocr_text):
    first_section = ocr_text[:300].lower()
    return "universitas gadjah mada" in first_section

def classify_document(ocr_text):
    patterns = {
        "surat_permohonan": r"(?i)\b(permohonan|memohon|mohon|bersedia\suntuk)\b",
        "surat_tugas": r"(?i)\b(surat tugas|memberikan tugas|kepada yang bersangkutan)\b",
        "surat_kuasa": r"(?i)\b(surat kuasa|memberi wewenang|pemberi kuasa|penerima kuasa)\b",
        "berita_acara": r"(?i)\b(berita acara|rangkaian acara)\b",
        "nota_dinas": r"(?i)\b(nota dinas|hormat saya|hal\s*:\s*)\b"
    }

    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            return category.title().replace("_", " ")

    return "Tidak Diketahui"

def detect_patterns(text, type):
    patterns = {
        "Surat Permohonan": {
            "nomor_surat": r"NOMOR\s*:\s*(\S+)",
            "pengirim": r"Dari\s*:\s*([^\n]+)",
            "tujuan": r"Kepada\s*:\s*([^\n]+)"
        },
        "Surat Tugas": {
            "nomor_surat": r"\b(\d+/UN[1I]/[A-Z0-9.-]+/[A-Z]+/[A-Z]+/\d{4})\b",
            "isi_surat": r"(Yang bertanda tangan.*?)mestinya\.",
            "ttd_surat": r"(Ketua|Dekan|Rektor|Direktur)[\s,]*\s*([\w\s.,-]+)\s*NIP\.?\s*(\d+)",
            "penerima_surat": r"Kepada Yth\.\s*([\w\s.,-]+)"
        },
        "default": {
            "nomor_surat": r"NOMOR\s*:\s*(\S+)",
            "pengirim": r"Asal\s*:\s*([^\n]+)"
        }
    }

    result = {}
    pattern_set = patterns.get(type, patterns["default"])

    for key, regex in pattern_set.items():
        match = re.search(regex, text, re.IGNORECASE | re.DOTALL)
        if match:
            # Ambil group pertama yang cocok
            result[key] = match.group(1).strip() if match.lastindex >= 1 else match.group().strip()

    return result

@app.route("/")
def hello_world():
    """Example Hello World route."""
    name = os.environ.get("NAME", "World")
    return f"Hello {name}!"

@app.route('/process_pdf', methods=['POST'])
def process_pdf():
    start = time.time()
    data = request.get_json()
    pdf_url = data.get('pdf_url')
    POPPLER = r'C:\poppler-24.08.0\Library\bin'

    if not pdf_url:
        return jsonify({
            "success": False,
            "message": "Missing 'pdf_url' in request"
        }), 400

    temp_dir = tempfile.mkdtemp()

    try:
        # 1. Download PDF
        download_start = time.time()
        local_pdf_path = download_pdf(pdf_url, temp_dir)
        print(f"[TIME] Download PDF: {time.time() - download_start:.2f}s")

        # 2. Convert PDF ke gambar
        convert_start = time.time()
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)
        print(f"[TIME] Convert PDF to images: {time.time() - convert_start:.2f}s")

        image_paths = []
        for i, image in enumerate(images):
            img_path = os.path.join(temp_dir, f"page_{i+1}.png")
            image.save(img_path, "PNG")
            image_paths.append(img_path)

        # 3. Lakukan OCR per halaman
        ocr_start = time.time()
        full_ocr_text = perform_ocr_on_images(temp_dir)
        print(f"[TIME] OCR selesai: {time.time() - ocr_start:.2f}s")

        # 4. Cek format UGM
        ugm_check_start = time.time()
        is_ugm = is_ugm_format(full_ocr_text)
        print(f"[TIME] Cek format UGM: {time.time() - ugm_check_start:.2f}s")

        if not is_ugm:
            return jsonify({
                "success": False,
                "message": "Bukan format Universitas Gadjah Mada"
            })

        # 5. Deteksi jenis surat
        detect_start = time.time()
        letter_type = classify_document(full_ocr_text)
        print(f"[TIME] Cek format UGM: {time.time() - detect_start:.2f}s")

        if letter_type == "Tidak Diketahui":
            return jsonify({
                "success": True,
                "is_ugm_format": True,
                "letter_type": "Tidak Diketahui",
                "ocr_text": full_ocr_text,
                "extracted_fields": {}
            })

        # 6. Ekstrak field sesuai jenis surat
        extract_start = time.time()
        extracted_fields = detect_patterns(full_ocr_text, letter_type)
        print(f"[TIME] Cek format UGM: {time.time() - extract_start:.2f}s")

        return jsonify({
            "success": True,
            "is_ugm_format": True,
            "letter_type": letter_type,
            "ocr_text": full_ocr_text,
            "extracted_fields": extracted_fields
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:
        # Bersihkan folder sementara
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))