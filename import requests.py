import requests
from google.cloud import storage, bigquery
import datetime

API_URL = "https://api.cursor.com"
API_KEY = "crsr_314d12267c0283f387098784ad188c9cdd0b3370b0088c4c9ac41fe11b8930c4:"

BUCKET = " cursor_logs_bucket"
DATASET = "cursor_logs_dataset"
TABLE = "cursor_raw_logs"

def run_pipeline(request):
    # 1. Fetch data
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(API_URL, headers=headers)

    if response.status_code != 200:
        return "API failed", 500

    # 2. File name
    today = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_name = f"cursor_export_{today}.json"

    # 3. Upload to GCS
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET)
    blob = bucket.blob(file_name)
    blob.upload_from_string(response.content)

    # 4. Load to BigQuery
    bq_client = bigquery.Client()
    uri = f"gs://{BUCKET}/{file_name}"

    job = bq_client.load_table_from_uri(
        uri,
        f"{DATASET}.{TABLE}",
        job_config=bigquery.LoadJobConfig(
            autodetect=True,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        ),
    )
    job.result()

    return "Success"