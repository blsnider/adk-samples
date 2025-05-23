import os
import json
from datetime import timedelta
import logging
from flask import Flask, render_template, request
from google.cloud import storage, bigquery, documentai_v1 as documentai
from vertexai.preview.generative_models import GenerativeModel
import vertexai

os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "sis-2025-hackathon-35428220c444.json")

project_id = "sis-2025-hackathon"
location = "us"
processor_id = "689768479d899f32"
bucket_name = "ltl_invoice"
upload_prefix = "invoices/"
parsed_prefix = "parsed/"
bq_dataset = "invoice_data"
bq_table = "invoices"

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    filename="webapp.log",
)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    files = request.files.getlist('files')
    if not files or files[0].filename == "":
        logger.warning("No files uploaded")
        return render_template('error.html', message='No PDF files provided.')
    results = []
    try:
        for file in files:
            filename = file.filename
            logger.info("Processing %s", filename)
            blob_path = f"{upload_prefix}{filename}"
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.upload_from_file(file, content_type='application/pdf')

            content = blob.download_as_bytes()
            doc = parse_documentai(content)
            parsed = extract_fields(doc)
            parsed["summary"] = generate_summary(parsed)
            parsed["gcs_uri"] = f"gs://{bucket_name}/{blob_path}"
            parsed["view_url"] = blob.generate_signed_url(version="v4", expiration=timedelta(minutes=15), method="GET")

            duplicates = check_duplicate(parsed.get("invoice_id"), parsed.get("supplier_name"))
            if duplicates:
                logger.info("Duplicate detected for %s", filename)
                return render_template('duplicate.html', invoice=parsed, matches=duplicates)

            json_blob = bucket.blob(f"{parsed_prefix}{filename.replace('.pdf', '.json')}")
            json_blob.upload_from_string(json.dumps(parsed, indent=2), content_type="application/json")

            bq_client = bigquery.Client()
            table_ref = bq_client.dataset(bq_dataset).table(bq_table)
            bq_client.insert_rows_json(table_ref, [parsed])

            results.append(parsed)
        logger.info("Successfully processed %d invoice(s)", len(results))
        return render_template('result.html', invoices=results)
    except Exception as e:
        logger.exception("Failed to process upload")
        return render_template('error.html', message=str(e)), 500

@app.route('/admin')
def admin():
    try:
        bq_client = bigquery.Client()
        total_query = f"SELECT COUNT(*) AS count FROM `{project_id}.{bq_dataset}.{bq_table}`"
        total_count = list(bq_client.query(total_query))[0].count
        totals_query = f"""
            SELECT supplier_name AS vendor, COUNT(*) AS count, SUM(CAST(total_amount AS FLOAT64)) AS amount
            FROM `{project_id}.{bq_dataset}.{bq_table}`
            GROUP BY vendor
        """
        totals = [dict(row) for row in bq_client.query(totals_query)]
        logger.info("Admin stats requested")
        return render_template('admin.html', total_count=total_count, totals=totals)
    except Exception as e:
        logger.exception("Failed to load admin page")
        return render_template('error.html', message=str(e)), 500

def parse_documentai(content_bytes):
    client = documentai.DocumentProcessorServiceClient()
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    raw_doc = documentai.RawDocument(content=content_bytes, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_doc)
    result = client.process_document(request=request)
    return result.document

def extract_fields(document):
    fields = {}
    confidences = []
    for entity in document.entities:
        key = entity.type_.strip().lower()
        val = entity.mention_text.replace("\n", " ").strip()
        fields[key] = val
        if entity.confidence is not None:
            confidences.append(entity.confidence)
    avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    fields["document_confidence"] = avg_conf
    return fields

def check_duplicate(invoice_id, supplier_name):
    if not invoice_id or not supplier_name:
        return []
    bq_client = bigquery.Client()
    query = f"""
        SELECT * FROM `{project_id}.{bq_dataset}.{bq_table}`
        WHERE invoice_id = @invoice_id AND supplier_name = @supplier_name
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("invoice_id", "STRING", invoice_id),
        bigquery.ScalarQueryParameter("supplier_name", "STRING", supplier_name),
    ])
    result = bq_client.query(query, job_config=job_config)
    return [dict(row) for row in result]

def generate_summary(parsed_fields):
    vertexai.init(project=project_id, location="us-central1")
    model = GenerativeModel("gemini-2.0-flash-lite-001")
    prompt = f"""
    Create a one-sentence summary of this invoice:
    - Invoice ID: {parsed_fields.get('invoice_id', '')}
    - Supplier: {parsed_fields.get('supplier_name', '')}
    - Receiver: {parsed_fields.get('receiver_name', '')}
    - Amount: {parsed_fields.get('total_amount', '')} {parsed_fields.get('currency', '')}
    - Due date: {parsed_fields.get('due_date', '')}
    - Terms: {parsed_fields.get('payment_terms', '')}
    - Carrier: {parsed_fields.get('carrier', '')}
    """
    try:
        response = model.generate_content(prompt.strip())
        return response.text.strip()
    except Exception as e:
        return f"(Failed to generate summary: {e})"

@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception("Unhandled error")
    return render_template('error.html', message=str(e)), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
