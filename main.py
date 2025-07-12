import os
import re
import json
import time
import shutil
import tempfile
import requests
import pytesseract
import cv2
import editdistance 
from kbbi import KBBI
import unicodedata

from flask import Flask, request, jsonify
from pdf2image import convert_from_path
from threading import Thread

app = Flask(__name__)

# --- IMPORTANT: Configure these paths for your local setup ---
# Path to the 'bin' directory of your Poppler installation
# Make sure Poppler is installed and configured correctly.
# For Windows: POPPLER = r'C:\path\to\poppler-xxx\Library\bin'
# For macOS/Linux: POPPLER = r'/opt/local/bin' or check your system's Poppler path
POPPLER = r'/opt/local/bin' 

# --- Path to your Ground Truth data ---
# Pastikan direktori ini ada dan berisi file JSON ground truth Anda
GROUND_TRUTH_DIR = './testing/keterangan' 
# GROUND_TRUTH_DIR = './testing/permohonan' 
# GROUND_TRUTH_DIR = './testing/tidak_diketahui' 
# GROUND_TRUTH_DIR = './testing/tugas' 

# --- Keywords for document grouping ---
TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Tugas", "Surat Keterangan",
                  "Surat Kuasa", "Surat Pelimpahan Wewenang", "Surat Edaran",
                  "Berita Acara", "Nota Dinas", "Keputusan", "Laporan", "Peraturan"]

SALUTATION_KEYWORDS = ["Yth.", "Yang Terhormat", "Kepada"]
REGULATION_KEYWORDS = ["Keputusan tentang", "Peraturan tentang", "No."]

# --- Inisialisasi KBBI (tetap di awal aplikasi) ---
KBBI_ENGINE = None
try:
    # Coba lakukan pencarian dummy untuk memicu koneksi awal dan memastikan inisialisasi sukses
    _ = KBBI('contoh')
    KBBI_ENGINE = KBBI
    print("KBBI berhasil diinisialisasi dan siap digunakan.")
except Exception as e:
    print(f"Peringatan: Gagal menginisialisasi KBBI atau koneksi bermasalah: {e}")
    print("Fungsi normalisasi/sinonim KBBI mungkin tidak bekerja.")

# --- Fungsi Bantuan Tesaurus ---
def get_all_forms_from_kbbi(base_term):
    """
    Mengembalikan daftar semua bentuk kata/frasa (termasuk kata dasar dan sinonimnya)
    menggunakan kbbi-python. Mengembalikan [base_term] jika KBBI tidak tersedia atau ada error.
    """
    if KBBI_ENGINE is None:
        return [base_term] # Fallback jika KBBI tidak terinisialisasi atau koneksi gagal

    synonyms = []
    try:
        # Cari kata di KBBI
        result = KBBI_ENGINE(base_term.lower()) # Cari dengan huruf kecil untuk konsistensi
        
        # Tambahkan lema utama jika ada
        if result and hasattr(result, 'lema') and result.lema:
            synonyms.append(result.lema.lower())

        # Tambahkan sinonim jika ada
        if result and hasattr(result, 'sinonim') and result.sinonim:
            synonyms.extend([s.lower() for s in result.sinonim])
            
        # Hapus duplikat dan pastikan unik
        synonyms = list(set(synonyms))
        
        # Tambahkan kata dasar itu sendiri ke daftar jika belum ada
        if base_term.lower() not in synonyms:
            synonyms.insert(0, base_term.lower()) # Prioritaskan kata dasar
            
    except Exception as e:
        # print(f"Error saat mencari sinonim '{base_term}' di KBBI: {e}") # Bisa di-uncomment untuk debugging
        synonyms = [base_term.lower()] # Fallback ke kata dasar jika ada error

    return synonyms

# --- Fungsi Pra-pemrosesan Teks Baru ---
def normalize_text_with_thesaurus(text):
    """
    Melakukan normalisasi teks dasar menggunakan tesaurus/KBBI.
    Ini adalah implementasi sederhana untuk contoh.
    Untuk produksi, butuh spell checker dan logic yang lebih robust.
    """
    if not KBBI_ENGINE:
        return text # Jika KBBI tidak aktif, kembalikan teks asli

    normalized_words = []
    # Tokenisasi teks menjadi kata-kata. Lebih baik pakai pustaka NLP seperti NLTK/SpaCy untuk ini
    words = re.findall(r'\b\w+\b', text.lower()) # Ambil semua kata, ubah ke lowercase

    for word in words:
        # Contoh sederhana: Coba cari sinonim. Jika ada, gunakan lema utama/bentuk standar.
        # Ini BUKAN spell checker penuh, hanya menunjukkan bagaimana tesaurus bisa membantu.
        found_forms = get_all_forms_from_kbbi(word)
        if word in found_forms and found_forms[0] != word: # Jika kata dasar punya sinonim dan bukan kata itu sendiri
            normalized_words.append(found_forms[0]) # Gunakan bentuk standar/lema dari tesaurus
        else:
            normalized_words.append(word) # Pertahankan kata asli

    return ' '.join(normalized_words)

# --- Helper Functions (Existing functions remain, no core logic changes) ---
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

def download_pdf(pdf_path_or_url, output_dir):
    """
    Downloads PDF from a URL or copies from a local path.
    Returns the local path of the PDF.
    """
    local_pdf_filename = os.path.basename(pdf_path_or_url) # Get filename from path/url
    local_pdf_path = os.path.join(output_dir, local_pdf_filename)
    
    # Check if it's a URL (starts with http:// or https://)
    if pdf_path_or_url.startswith(('http://', 'https://')):
        print(f"Attempting to download PDF from {pdf_path_or_url} to {local_pdf_path}")
        try:
            response = requests.get(pdf_path_or_url, stream=True)
            response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
            with open(local_pdf_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
            print(f"PDF downloaded successfully to {local_pdf_path}")
            return local_pdf_path
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to download PDF from {pdf_path_or_url}: {e}")
        except Exception as e:
            raise Exception(f"An unexpected error occurred during PDF download: {e}")
    else:
        # Assume it's a local file path
        if not os.path.exists(pdf_path_or_url):
            raise FileNotFoundError(f"Local PDF file not found at: {pdf_path_or_url}")
        print(f"Copying local PDF from {pdf_path_or_url} to {local_pdf_path}")
        shutil.copy(pdf_path_or_url, local_pdf_path)
        print(f"PDF copied successfully to {local_pdf_path}")
        return local_pdf_path


def perform_ocr_and_get_page_texts(image_dir):
    """Performs OCR on images and returns a dictionary of page_path: text."""
    ocr_results_per_page = {}
    for filename in sorted(os.listdir(image_dir), key=lambda f: int(re.search(r'\d+', f).group()) if re.search(r'\d+', f) else 0):
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
    # Sekarang, ocr_text yang masuk ke sini sudah bisa diasumsikan lebih bersih dari normalize_text_with_thesaurus
    # Namun, untuk akurasi lebih, kita tetap bisa pakai sinonim di regex
    surat_tugas_forms = get_all_forms_from_kbbi("surat tugas")
    surat_keterangan_forms = get_all_forms_from_kbbi("surat keterangan")
    surat_permohonan_forms = get_all_forms_from_kbbi("surat permohonan")

    patterns = {
        "surat_tugas": r"(?i)\b(" + "|".join(re.escape(s) for s in surat_tugas_forms) + r")\b",
        "surat_keterangan": r"(?i)\b(" + "|".join(re.escape(s) for s in surat_keterangan_forms) + r")\b",
        "surat_permohonan": r"(?i)\b(" + "|".join(re.escape(s) for s in surat_permohonan_forms) + r")\b",
    }

    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            return category.title().replace("_", " ")
    print('Document type classified as "Tidak Diketahui".')
    return "Tidak Diketahui"


# Fungsi detect_patterns juga sama, beroperasi pada teks yang sudah dinormalisasi dan menggunakan sinonim di regex
def detect_patterns(text, letter_type):
    nomor_surat_keywords = get_all_forms_from_kbbi("nomor")
    isi_surat_keywords = get_all_forms_from_kbbi("dengan hormat")
    penerima_surat_keywords = get_all_forms_from_kbbi("yth")
    tanggal_keywords = get_all_forms_from_kbbi("tanggal")

    base_patterns = {
        "nomor_surat": r"(?i)\b(" + "|".join(re.escape(s) for s in nomor_surat_keywords) + r")\s*[:]?\s*(\d+/UN[1I][A-Z0-9.-]*\/[A-Z0-9.-]+\/[A-Z]+\/[A-Z]+\/\d{4})",
        "isi_surat": r"((?:" + "|".join(re.escape(s) for s in isi_surat_keywords) + r").*?terima kasih)",
        "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
        "penerima_surat": r"(?:" + "|".join(re.escape(s) for s in penerima_surat_keywords) + r")\s*(.*?)\s*(?:Dengan|Hormat)",
        "tanggal": r"(?i)\b(" + "|".join(re.escape(s) for s in tanggal_keywords) + r")[:]?\s*([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
    }

    patterns = {
        "Surat Permohonan": {
            "nomor_surat": base_patterns["nomor_surat"],
            "isi_surat": base_patterns["isi_surat"],
            "ttd_surat": base_patterns["ttd_surat"],
            "penerima_surat": base_patterns["penerima_surat"],
            "tanggal": base_patterns["tanggal"]
        },
        "Surat Tugas": {
            "nomor_surat": base_patterns["nomor_surat"],
            "isi_surat": r"(Yang bertanda tangan.*?(?:mestinya|semestinya)\.)",
            "ttd_surat": base_patterns["ttd_surat"],
            "tanggal": base_patterns["tanggal"]
        },
        "Surat Keterangan": {
           "nomor_surat": base_patterns["nomor_surat"],
            "isi_surat": r"(Yang bertanda tangan.*?(?:mestinya|semestinya)\.)",
            "ttd_surat": base_patterns["ttd_surat"],
            "tanggal": base_patterns["tanggal"]
        },
        "default": {
            "nomor_surat": base_patterns["nomor_surat"],
            "ttd_surat": base_patterns["ttd_surat"],
            "tanggal": base_patterns["tanggal"]
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

    print('Patterns detected.')
    return result

# --- Accuracy Calculation Functions ---
def load_ground_truth(doc_filename):
    """Loads ground truth data for a given document filename."""
    base_filename = os.path.basename(doc_filename).replace('.pdf', '.json')
    gt_path = os.path.join(GROUND_TRUTH_DIR, base_filename)
    
    print(f"Attempting to load ground truth from: {gt_path}")
    if os.path.exists(gt_path):
        try:
            with open(gt_path, 'r', encoding='utf-8') as f:
                gt_data = json.load(f)
                print(f"Ground truth loaded successfully for {base_filename}")
                return gt_data
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from {gt_path}: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred while reading {gt_path}: {e}")
            return None
    print(f"Ground truth file not found at {gt_path}")
    return None

def calculate_cer(ground_truth_text, ocr_text):
    """Calculates Character Error Rate (CER)."""
    if not ground_truth_text and not ocr_text: return 0.0 # Both empty, 0 error
    if not ground_truth_text: return 1.0 # Only ground_truth empty, max error
    
    return editdistance.eval(ground_truth_text, ocr_text) / len(ground_truth_text)

def calculate_wer(ground_truth_text, ocr_text):
    """Calculates Word Error Rate (WER)."""
    gt_words = ground_truth_text.split()
    ocr_words = ocr_text.split()
    
    if not gt_words and not ocr_words: return 0.0 # Both empty, 0 error
    if not gt_words: return 1.0 # Only ground_truth empty, max error

    return editdistance.eval(gt_words, ocr_words) / len(gt_words)

def calculate_field_accuracy(predicted_fields, ground_truth_fields):
    """Calculates accuracy for extracted fields."""
    field_accuracies = {}
    total_fields_to_check = 0
    correctly_extracted_fields = 0

    if isinstance(ground_truth_fields, str):
        try:
            ground_truth_fields = json.loads(ground_truth_fields)
        except json.JSONDecodeError:
            ground_truth_fields = {}

    for field_name, gt_value in ground_truth_fields.items():
        total_fields_to_check += 1
        predicted_data = predicted_fields.get(field_name)

        if predicted_data and 'text' in predicted_data:
            predicted_value = predicted_data['text']
            is_correct = (str(predicted_value).strip().lower() == str(gt_value).strip().lower())
            field_accuracies[field_name] = {
                "correct": is_correct,
                "predicted": predicted_value,
                "ground_truth": gt_value
            }
            if is_correct:
                correctly_extracted_fields += 1
        else:
            field_accuracies[field_name] = {
                "correct": False,
                "predicted": None,
                "ground_truth": gt_value,
                "status": "Not Extracted"
            }
    
    overall_field_accuracy = (correctly_extracted_fields / total_fields_to_check) * 100 if total_fields_to_check > 0 else 0.0
    
    return overall_field_accuracy, field_accuracies

# --- Flask Routes (These remain for the API functionality, but are not used in direct local testing) ---

@app.route("/")
def index():
    return "Hello from Flask OCR!"

@app.route("/submit_pdf", methods=["POST"])
def submit_pdf():
    data = request.get_json()
    task_id = data.get("task_id")
    pdf_url = data.get("pdf_url") # This would be a local path in your scenario

    if not pdf_url or not task_id:
        return jsonify({"success": False, "message": "Missing 'pdf_url' or 'task_id'"}), 400

    Thread(target=background_process, args=(pdf_url, task_id)).start()
    return jsonify({"success": True, "message": "Job accepted and processing in background"}), 202

# --- Modified background_process function with print formatting ---
def background_process(pdf_path_or_url, task_id):
    """
    Processes a PDF file (either local path or URL) and performs OCR,
    document classification, and accuracy calculation.
    Prints the testing results to the console.
    """
    print(f'Starting OCR process for task_id: {task_id} with PDF: {pdf_path_or_url}')
    temp_dir = tempfile.mkdtemp()
    try:
        local_pdf_path = download_pdf(pdf_path_or_url, temp_dir)
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)

        for i, image in enumerate(images):
            image.save(os.path.join(temp_dir, f"page_{i+1}.png"), "PNG")

        ocr_results_per_page = perform_ocr_and_get_page_texts(temp_dir)
        grouped_documents = group_pages(ocr_results_per_page)

        all_processed_docs = []
        for i, doc_text_original in enumerate(grouped_documents):
            # --- Perubahan di sini: Normalisasi Teks menggunakan KBBI ---
            doc_text_normalized = normalize_text_with_thesaurus(doc_text_original)
            print(f"\n[ Teks Asli (Setelah Grouping) ]:\n{doc_text_original[:200]}...") # Print sebagian
            print(f"\n[ Teks Hasil Normalisasi KBBI ]:\n{doc_text_normalized[:200]}...") # Print sebagian
            
            # --- Gunakan teks yang sudah dinormalisasi untuk proses selanjutnya ---
            is_ugm = is_ugm_format(doc_text_normalized)
            letter_type = classify_document(doc_text_normalized)
            print("------- type : ", letter_type)
            extracted_fields = detect_patterns(doc_text_normalized, letter_type)
            
            ground_truth_data = load_ground_truth(os.path.basename(local_pdf_path)) 

            accuracy_metrics = {}
            if ground_truth_data:
                # --- START: Output Format for OCR Testing Results ---
                print("\n" + "="*70)
                print(f"Pengujian Dokumen #{i+1} dari {os.path.basename(local_pdf_path)}")
                print("="*70)

                gt_full_text = ground_truth_data.get('full_text', '')
                
                print("\n[ Teks Asli (Ground Truth) ]")
                print("-" * 35)
                print(gt_full_text if gt_full_text else 'Tidak ada teks asli ditemukan dalam Ground Truth.')
                print("-" * 35)

                print("\n[ Teks Hasil OCR (Original) ]")
                print("-" * 35)
                print(repr(doc_text_original) if doc_text_original else 'Tidak ada teks hasil OCR.')
                print("-" * 35)

                print("\n[ Teks Hasil OCR (Normalized) ]") # Tampilkan teks yang dinormalisasi juga
                print("-" * 35)
                print(repr(doc_text_normalized) if doc_text_normalized else 'Tidak ada teks hasil OCR yang dinormalisasi.')
                print("-" * 35)

                # Hitung CER/WER dengan teks normalisasi jika ground truth juga normal
                # Atau, bandingkan teks OCR asli dengan ground truth, tergantung metodologi Anda
                cer = calculate_cer(gt_full_text, doc_text_original) # Bandingkan original dengan GT
                wer = calculate_wer(gt_full_text, doc_text_original) # Bandingkan original dengan GT
                
                overall_field_acc, individual_field_acc_details = calculate_field_accuracy(
                    extracted_fields, ground_truth_data.get('extracted_fields', {})
                )
                
                accuracy_metrics = {
                    "cer": cer,
                    "wer": wer,
                    "overall_field_accuracy": overall_field_acc,
                    "individual_field_accuracy": individual_field_acc_details
                }

                print("\n[ Metrik Akurasi ]")
                print("-" * 35)
                print(f"  Character Error Rate (CER):        {accuracy_metrics['cer'] * 100:.2f}%")
                print(f"  Word Error Rate (WER):             {accuracy_metrics['wer'] * 100:.2f}%")
                print(f"  Akurasi Ekstraksi Field Keseluruhan: {accuracy_metrics['overall_field_accuracy']:.2f}%")
                
                print("\n  Detail Akurasi Field Individual:")
                if accuracy_metrics['individual_field_accuracy']:
                    for field, details in accuracy_metrics['individual_field_accuracy'].items():
                        status = "Match" if details['correct'] else "Mismatch"
                        if 'status' in details:
                            status = details['status']

                        predicted_val_display = f"'{details['predicted']}'" if details['predicted'] is not None else "N/A"
                        gt_val_display = f"'{details['ground_truth']}'" if details['ground_truth'] is not None else "N/A"
                        
                        if status == "Match":
                            print(f"    - {field}: {status} (Value: {predicted_val_display})")
                        elif status == "Mismatch":
                            print(f"    - {field}: {status} (Expected: {gt_val_display}, Got: {predicted_val_display})")
                        else:
                            print(f"    - {field}: {status} (Expected: {gt_val_display})")
                else:
                    print("    Tidak ada field yang diekstrak atau dicocokkan dari Ground Truth.")
                print("-" * 35)
                print("="*70 + "\n")
                # --- END: Output Format for OCR Testing Results ---

            all_processed_docs.append({
                "document_index": i + 1,
                "is_ugm_format": is_ugm,
                "letter_type": letter_type,
                "ocr_text_original": doc_text_original, # Simpan teks asli
                "ocr_text_normalized": doc_text_normalized, # Simpan teks yang sudah dinormalisasi
                "extracted_fields": extracted_fields,
                "accuracy_metrics": accuracy_metrics 
            })

    except Exception as e:
        print(f"Error in background_process for task_id {task_id}: {e}")
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    # --- IMPORTANT: Konfigurasi untuk Pengujian Lokal ---
    # 1. Pastikan Poppler terinstal dan POPPLER path diatur dengan benar di bagian atas kode.
    # 2. Buat direktori 'testing' di lokasi yang sama dengan script Python ini.
    # 3. Letakkan file PDF yang ingin Anda uji di suatu lokasi.
    # 4. Untuk setiap file PDF, buat file JSON ground truth yang sesuai di direktori 'testing'.
    #    Nama file JSON harus sama dengan nama file PDF, tetapi dengan ekstensi '.json'.
    #    Contoh: 'surat_tugas_01.pdf' -> 'surat_tugas_01.json'

    # Contoh struktur file JSON ground truth (misal untuk 'surat_tugas_01.json'):
    # {
    #   "full_text": "Teks lengkap dari surat tugas ini sesuai aslinya untuk CER/WER.",
    #   "extracted_fields": {
    #     "nomor_surat": "123/UN1/SK/2023",
    #     "tanggal": "Yogyakarta, 10 Juli 2024",
    #     "ttd_surat": "Prof. Dr. Nama Pejabat, M.Sc."
    #     # Tambahkan field lain yang relevan yang Anda ekstrak dan ingin validasi
    #   }
    # }

    # Contoh penggunaan:
    # Ganti 'path/to/your/local_document.pdf' dengan jalur PDF aktual Anda.
    # Anda bisa menambahkan beberapa file PDF untuk diuji secara berurutan.
    
    # Pastikan direktori GROUND_TRUTH_DIR ada
    if not os.path.exists(GROUND_TRUTH_DIR):
        os.makedirs(GROUND_TRUTH_DIR)
        print(f"Created ground truth directory: {GROUND_TRUTH_DIR}")

    # ---------------- SURAT PERMOHONAN -------------------------
    # pdf_files_to_test = [
    #     r'./file_pdf/permohonan/23774.pdf',
    #     r'./file_pdf/permohonan/20250124_083609.pdf',
    #     r'./file_pdf/permohonan/Scan.pdf',
    #     r'./file_pdf/permohonan/Pengantar Penelitian PA John Feri Jr. Ramadhan TRPL.pdf',
    #     r'./file_pdf/permohonan/Pengantar PI  M.Reynaldi Maso dkk TRIK.pdf',
    # ]
    # ---------------- SURAT TUGAS -------------------------
    # pdf_files_to_test = [
    #     r'./file_pdf/tugas/5163 Surat Tugas MTQMN 3-10 November 2023.pdf',
    #     r'./file_pdf/tugas/Surat Tugas MAGANG  Sigit Yunianto TRE.pdf',
    #     r'./file_pdf/tugas/20250124_083620.pdf',
    #     r'./file_pdf/tugas/Surat Tugas MAGANG Rosus Pangaribowo dkk TRE-1.pdf',
    #     r'./file_pdf/tugas/Surat Tugas MAGANG Rosus Pangaribowo dkk TRE-2.pdf',
    # ]
    # ---------------- SURAT KETERANGAN -------------------------
    pdf_files_to_test = [
        r'./file_pdf/keterangan/SKAK an.Devina Dwiyanti.pdf',
        # r'./file_pdf/keterangan/8 - Surat Aktif Adiyatma Hilmy.pdf',
        # r'./file_pdf/keterangan/2025-04_479064_345_Ridha_Fauziyya_Rahma_24_Genap (1).pdf',
        # r'./file_pdf/keterangan/20250212_144606.pdf',
        # r'./file_pdf/keterangan/SKAK an.Alya Zakhira Anjani-paraf (1).pdf',
    ]
    # ---------------- TIDAK TERKLASIFIKASI -------------------------
    # pdf_files_to_test = [
        # r'./file_pdf/tidak_diketahui/Surat Edaran Peringatan Peniupuan Mengatasnamakan Pimpinan UGM.pdf',
        # r'./file_pdf/tidak_diketahui/BA Pendadaran  an. ALYA ZAKHIRA ANJANI.xlsx - Undangan.pdf',
        # r'./file_pdf/tidak_diketahui/Surat Rekomendasi Program Magenta Dicky Ardiansyah Pramana Putra,2 (1) (2).pdf',
        # r'./file_pdf/tidak_diketahui/Surat Rekomendasi BEM KM SV UGM.pdf',
        # r'./file_pdf/tidak_diketahui/Surat Rekomendasi_Vellya Riona.pdf',
    # ]
    
    # --- PENTING: Ganti baris di bawah ini dengan file PDF yang ingin Anda uji! ---
    # Contoh:
    # pdf_files_to_test.append('./sample_documents/sample_surat_tugas.pdf')
    # Pastikan Anda memiliki './sample_documents/sample_surat_tugas.pdf' dan 
    # './testing/sample_surat_tugas.json' (sesuaikan nama file ground truth)

    if not pdf_files_to_test:
        print("Tidak ada file PDF yang dikonfigurasi untuk pengujian. Silakan tambahkan path PDF ke 'pdf_files_to_test'.")
    else:
        for i, pdf_path in enumerate(pdf_files_to_test):
            print(f"\n--- Memulai Pengujian untuk PDF: {pdf_path} ---")
            # Jalankan background_process secara langsung untuk pengujian lokal
            # Gunakan task_id unik untuk setiap pengujian jika Anda ingin melacaknya
            background_process(pdf_path, f"local_test_{i+1}")
            print(f"--- Pengujian Selesai untuk PDF: {pdf_path} ---\n")

    # Anda tidak perlu menjalankan Flask app jika Anda hanya ingin menjalankan pengujian OCR.
    # Jika Anda ingin tetap menjalankan Flask API (tanpa koneksi Laravel) untuk tujuan lain,
    # biarkan baris di bawah ini tidak dikomentari.
    # app.run(host="0.0.0.0", port=3000, debug=True, threaded=True)