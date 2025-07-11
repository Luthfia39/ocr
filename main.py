import os
import re
import json
import time
import shutil
import tempfile
import requests
import pytesseract
import cv2
import editdistance # Make sure to install: pip install editdistance

from flask import Flask, request, jsonify
from pdf2image import convert_from_path
from threading import Thread

app = Flask(__name__)

# --- IMPORTANT: Configure these paths for your local setup ---
# Path to the 'bin' directory of your Poppler installation
POPPLER = r'/opt/local/bin' # You might need to change this!
# For Windows: POPPLER = r'C:\path\to\poppler-xxx\Library\bin'

# --- Path to your Ground Truth data ---
# Pastikan direktori ini ada dan berisi file JSON ground truth Anda
GROUND_TRUTH_DIR = './testing' # e.g., 'C:\my_ocr_project\ground_truth_data' or './ground_truth_data'

# --- Keywords for document grouping ---
TITLE_KEYWORDS = ["Surat Pernyataan", "Surat Tugas", "Surat Keterangan",
                  "Surat Kuasa", "Surat Pelimpahan Wewenang", "Surat Edaran",
                  "Berita Acara", "Nota Dinas", "Keputusan", "Laporan", "Peraturan"]

SALUTATION_KEYWORDS = ["Yth.", "Yang Terhormat", "Kepada"]
REGULATION_KEYWORDS = ["Keputusan tentang", "Peraturan tentang", "No."]

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

def download_pdf(pdf_url, output_dir):
    local_pdf_path = os.path.join(output_dir, 'downloaded.pdf')
    print(f"Attempting to download PDF from {pdf_url} to {local_pdf_path}") # Added print
    try:
        response = requests.get(pdf_url, stream=True)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        with open(local_pdf_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                f.write(chunk)
        print(f"PDF downloaded successfully to {local_pdf_path}") # Added print
        return local_pdf_path
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download PDF from {pdf_url}: {e}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred during PDF download: {e}")

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
    patterns = {
        "surat_tugas": r"(?i)\b(surat tugas|yang bertanda tangan.*memberikan tugas kepada)\b",
        "surat_keterangan": r"(?i)\b(surat keterangan)\b",
        "surat_permohonan": r"(?i)\b(permohonan|sehubungan dengan.*terima kasih)\b",
    }
    for category, pattern in patterns.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            return category.title().replace("_", " ")
    return "Tidak Diketahui"

def detect_patterns(text, letter_type):
    patterns = {
        "Surat Permohonan": {
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Dengan hormat|Sehubungan dengan).*?terima kasih)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "penerima_surat": r"Yth\.\s*(.*?)\s*Dengan", 
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "Surat Tugas": { 
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Yang bertanda tangan|Yang bertandatangan).*?(?:mestinya|semestinya)\.)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "Surat Keterangan": { 
           "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "isi_surat": r"((?:Yang bertanda tangan|Yang bertandatangan).*?(?:mestinya|semestinya)\.)",
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
            "tanggal": r"([A-Za-z\s]+),\s*(\d{1,2}\s+(?:Januari|Jan|Februari|Feb|Maret|Mar|April|Apr|Mei|May|Juni|Jun|Juli|Jul|Agustus|Agu|September|Sep|Oktober|Okt|November|Nov|Desember|Des)\s+\d{4})"
        },
        "default": {
            "nomor_surat": r"(\d+/UN[1I]/?([A-Z0-9.-]+\/){1,3}\d{4})", 
            "ttd_surat": r"(?:a\.n\.|u\.b\.|n\.b\.)?\s*(?:Ketua|Dekan|Rektor|Direktur|Wakil Dekan|Kepala Departemen|Sekretaris).*?\s*([A-Za-z.,\s-]+)\s*(?:NIP\.?|NIKA\.?)\s*\d+",
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

# --- Accuracy Calculation Functions ---
def load_ground_truth(doc_filename):
    """Loads ground truth data for a given document filename."""
    # Ensure doc_filename is just the basename (e.g., 'document.pdf' not full URL)
    # And convert .pdf to .json for the ground truth file
    base_filename = os.path.basename(doc_filename).replace('.pdf', '.json')
    gt_path = os.path.join(GROUND_TRUTH_DIR, base_filename)
    
    print(f"Attempting to load ground truth from: {gt_path}") # Added print
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

    # Ensure ground_truth_fields is treated as a dict
    # If it's a string, try to parse it. If parsing fails, treat as empty.
    if isinstance(ground_truth_fields, str):
        try:
            ground_truth_fields = json.loads(ground_truth_fields)
        except json.JSONDecodeError:
            ground_truth_fields = {} # Fallback if not valid JSON string

    for field_name, gt_value in ground_truth_fields.items():
        total_fields_to_check += 1 # Count every field present in ground truth
        predicted_data = predicted_fields.get(field_name) # Get the dict for predicted field

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
            # Field not predicted or 'text' key missing in prediction
            field_accuracies[field_name] = {
                "correct": False,
                "predicted": None,
                "ground_truth": gt_value,
                "status": "Not Extracted" # Added status for clarity
            }
    
    overall_field_accuracy = (correctly_extracted_fields / total_fields_to_check) * 100 if total_fields_to_check > 0 else 0.0
    
    return overall_field_accuracy, field_accuracies

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
        return jsonify({"success": False, "message": "Missing 'pdf_url' or 'task_id'"}), 400

    # It's better to pass the ground truth filename/ID if you want accuracy in background
    Thread(target=background_process, args=(pdf_url, task_id)).start()
    return jsonify({"success": True, "message": "Job accepted and processing in background"}), 202

# --- Modified background_process function with print formatting ---
def background_process(pdf_url, task_id):
    print(f'Starting background process for task_id: {task_id}')
    temp_dir = tempfile.mkdtemp()
    try:
        local_pdf_path = download_pdf(pdf_url, temp_dir)
        images = convert_from_path(local_pdf_path, poppler_path=POPPLER)

        for i, image in enumerate(images):
            image.save(os.path.join(temp_dir, f"page_{i+1}.png"), "PNG")

        ocr_results_per_page = perform_ocr_and_get_page_texts(temp_dir)
        grouped_documents = group_pages(ocr_results_per_page)

        all_processed_docs = []
        for i, doc_text in enumerate(grouped_documents):
            is_ugm = is_ugm_format(doc_text)
            letter_type = classify_document(doc_text)
            extracted_fields = detect_patterns(doc_text, letter_type)
            
            # Use local_pdf_path basename for load_ground_truth as it reflects the original file name
            ground_truth_data = load_ground_truth(os.path.basename(local_pdf_path)) 

            accuracy_metrics = {}
            if ground_truth_data:
                # --- START: Output Format for OCR Testing Results ---
                print("\n" + "="*70) # Wider separator
                print(f"Pengujian Dokumen #{i+1} dari {os.path.basename(local_pdf_path)}")
                print("="*70)

                # Get ground truth full text, default to empty string if not found
                gt_full_text = ground_truth_data.get('full_text', '')
                
                print("\n[ Teks Asli (Ground Truth) ]")
                print("-" * 35) # Adjusted width
                print(gt_full_text if gt_full_text else 'Tidak ada teks asli ditemukan dalam Ground Truth.')
                print("-" * 35)

                print("\n[ Teks Hasil OCR ]")
                print("-" * 35) # Adjusted width
                print(repr(doc_text) if doc_text else 'Tidak ada teks hasil OCR.')
                print("-" * 35)

                cer = calculate_cer(gt_full_text, doc_text)
                wer = calculate_wer(gt_full_text, doc_text)
                
                overall_field_acc, individual_field_acc_details = calculate_field_accuracy(
                    extracted_fields, ground_truth_data.get('extracted_fields', {})
                )
                
                accuracy_metrics = {
                    "cer": cer,
                    "wer": wer,
                    "overall_field_accuracy": overall_field_acc,
                    "individual_field_accuracy": individual_field_acc_details # Store full details
                }

                print("\n[ Metrik Akurasi ]")
                print("-" * 35) # Adjusted width
                print(f"  Character Error Rate (CER):        {accuracy_metrics['cer'] * 100:.2f}%") # Multiply by 100 and format to 2 decimal places
                print(f"  Word Error Rate (WER):             {accuracy_metrics['wer'] * 100:.2f}%")   # Multiply by 100 and format to 2 decimal places
                print(f"  Akurasi Ekstraksi Field Keseluruhan: {accuracy_metrics['overall_field_accuracy']:.2f}%")
                
                print("\n  Detail Akurasi Field Individual:")
                if accuracy_metrics['individual_field_accuracy']:
                    for field, details in accuracy_metrics['individual_field_accuracy'].items():
                        status = "Match" if details['correct'] else "Mismatch"
                        if 'status' in details: # For "Not Extracted"
                            status = details['status']

                        predicted_val_display = f"'{details['predicted']}'" if details['predicted'] is not None else "N/A"
                        gt_val_display = f"'{details['ground_truth']}'" if details['ground_truth'] is not None else "N/A"
                        
                        if status == "Match":
                            print(f"    - {field}: {status} (Value: {predicted_val_display})")
                        elif status == "Mismatch":
                            print(f"    - {field}: {status} (Expected: {gt_val_display}, Got: {predicted_val_display})")
                        else: # Not Extracted
                            print(f"    - {field}: {status} (Expected: {gt_val_display})")
                else:
                    print("    Tidak ada field yang diekstrak atau dicocokkan dari Ground Truth.")
                print("-" * 35) # Adjusted width
                print("="*70 + "\n")
                # --- END: Output Format for OCR Testing Results ---

            all_processed_docs.append({
                "document_index": i + 1,
                "is_ugm_format": is_ugm,
                "letter_type": letter_type,
                "ocr_text": doc_text,
                "extracted_fields": extracted_fields,
                "accuracy_metrics": accuracy_metrics 
            })

        # The new_path variable is for the Laravel hook, not directly related to print
        new_path = pdf_url.split('suratMasuk/')[-1] if 'suratMasuk/' in pdf_url else os.path.basename(pdf_url)

        headers = {
            'Content-Type': 'application/json',  
            'Accept': 'application/json'         
        }
        try:
            response = requests.post("http://127.0.0.1:8000/api/hook", json={
                "task_id": task_id,
                "pdf_url": new_path,
                "processed_documents": all_processed_docs 
            }, headers=headers)
        
            print(f'Successfully sent results to Laravel. Status Code: {response.status_code}')
        except Exception as e:
            print(f"Failed to send results to Laravel: {e}")
    except Exception as e:
        print(f"Error in background_process for task_id {task_id}: {e}")
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    # Example usage (for local testing, you might need a dummy PDF and ground truth JSON)
    # Ensure GROUND_TRUTH_DIR exists and contains 'downloaded.json' if you use the dummy_pdf_url
    # dummy_pdf_url = "http://localhost:3000/dummy.pdf" # Replace with a real accessible PDF URL
    # with open("dummy.pdf", "w") as f: f.write("dummy content") # Create a dummy file for testing download
    # if not os.path.exists(GROUND_TRUTH_DIR): os.makedirs(GROUND_TRUTH_DIR)
    # with open(os.path.join(GROUND_TRUTH_DIR, "downloaded.json"), "w") as f:
    #     json.dump({"full_text": "Ini adalah teks asli yang harus dicocokkan.", "extracted_fields": {"nama": "Budi", "nim": "123456"}}, f)

    app.run(host="0.0.0.0", port=3000, debug=True, threaded=True)