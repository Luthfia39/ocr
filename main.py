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
POPPLER = r'C:\poppler-24.08.0\Library\bin'  # ← Ubah sesuai direktori Poppler kamu

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

def classify_document(ocr_text):
    patterns = {
        "surat_permohonan": r"(?i)\b(permohonan|memohon|mohon|bersedia untuk)\b",
        "surat_tugas": r"(?i)\b(surat tugas|memberikan tugas|kepada yang bersangkutan)\b",
        "surat_kuasa": r"(?i)\b(surat kuasa|memberi wewenang|pemberi kuasa|penerima kuasa)\b",
        "berita_acara": r"(?i)\b(berita acara|rangkaian acara)\b",
        "nota_dinas": r"(?i)\b(nota dinas|hormat saya|hal\s*:\s*)\b"
    }

    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            return category.title().replace("_", " ")
    print('jenis')
    return "Tidak Diketahui"

def detect_patterns(text, letter_type):
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
    pattern_set = patterns.get(letter_type, patterns["default"])

    for key, regex in pattern_set.items():
        match = re.search(regex, text, re.IGNORECASE | re.DOTALL)
        if match:
            result[key] = match.group(1).strip()
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

        # Kirim hasil ke Laravel
        headers = {
            'Content-Type': 'application/json',  
            'Accept': 'application/json'         
        }
        try:
            response = requests.post("http://127.0.0.1:8000/api/hook", json={
                "task_id": id,
                "pdf_url": pdf_url,
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
