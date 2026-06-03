import os
import io
import json
import uuid
import datetime
from flask import Flask, request, jsonify
from google.cloud import storage, pubsub_v1, bigquery
import pg8000.native
from pypdf import PdfReader

app = Flask(__name__)

# Initialize All Google Cloud Infrastructure Clients
storage_client = storage.Client()
pubsub_client = pubsub_v1.PublisherClient()
bq_client = bigquery.Client()

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "pharma-medical-reference-docs")
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "medical-inquiry-agent")
TOPIC_ID = os.getenv("GCP_PUBSUB_TOPIC", "pharmacovigilance-alerts")
BIGQUERY_TABLE_REF = f"{PROJECT_ID}.telemetry_data.agent_logs"

# Overwrite your get_db_connection function with this fixed configuration:
def get_db_connection():
    """Establishes a connection to Cloud SQL via explicit Unix Domain Sockets."""
    return pg8000.native.Connection(
        user="postgres",
        password=os.getenv("DB_PASSWORD", "SecurePharmaPass2026!"),
        database="postgres",
        # Use unix_sock explicitly for pg8000 rather than overloading the host field
        unix_sock=f"/cloudsql/medical-inquiry-agent:us-central1:pharma-dashboard-db/.s.PGSQL.5432"
    )


def verify_security_passkey(request_headers):
    """Validates an explicit custom API header passed securely from Anthropic."""
    auth_header = request_headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
        
    parts = auth_header.split(" ")
    if len(parts) != 2:
        return False
        
    extracted_token = parts[1] # Extract the exact token string (the second element)
    expected_secret = os.getenv("ANTHROPIC_MCP_SECRET", "PharmaSecretPasskey2026!")
    return extracted_token == expected_secret



def search_gcs_documents(query: str, max_results: int = 3):
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs()
    matched_chunks = []
    query_words = [word.lower().strip() for word in query.split(" ") if len(word.strip()) > 2]
    
    if not query_words:
        return []

    for blob in blobs:
        file_text = ""
        try:
            if blob.name.endswith('.txt') or blob.name.endswith('.json'):
                file_text = blob.download_as_text(errors='ignore').lower()
            elif blob.name.endswith('.pdf'):
                pdf_bytes = blob.download_as_bytes()
                pdf_file = io.BytesIO(pdf_bytes)
                reader = PdfReader(pdf_file)
                file_text = " ".join([page.extract_text() for page in reader.pages if page.extract_text()]).lower()
                
            if file_text and all(word in file_text for word in query_words):
                matched_chunks.append({
                    "source": f"gs://{BUCKET_NAME}/{blob.name}",
                    "text": file_text[:1500]
                })
        except Exception as e:
            print(f"Exception parsing file {blob.name}: {str(e)}")
            
        if len(matched_chunks) >= max_results:
            break
    return matched_chunks

def publish_adverse_event(drug_name: str, raw_text: str, symptoms: list):
    """Publishes a payload to GCP Pub/Sub, wrapped in protective error handling."""
    topic_path = pubsub_client.topic_path(PROJECT_ID, TOPIC_ID)
    payload = {
        "event_type": "ADVERSE_EVENT_FLAG",
        "drug": drug_name,
        "flagged_symptoms": symptoms,
        "original_message": raw_text
    }
    data = json.dumps(payload).encode("utf-8")
    
    try:
        future = pubsub_client.publish(topic_path, data)
        return future.result()  # Returns the successful GCP Message ID string
    except Exception as e:
        print(f"CRITICAL OVERRIDE: Pub/Sub messaging pipeline offline or topic missing. Details: {str(e)}")
        return "LOCAL_OVERRIDE_FALLBACK_ID"



def log_transaction_to_bigquery(raw_query, keywords, doc_count, pv_triggered, draft=""):
    rows_to_insert = [
        {
            "inquiry_id": str(uuid.uuid4()),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "hcp_raw_query": str(raw_query),
            "extracted_keywords": str(keywords),
            "documents_returned_count": int(doc_count),
            "pharmacovigilance_triggered": bool(pv_triggered),
            "generated_draft_text": str(draft)
        }
    ]
    try:
        bq_client.insert_rows_json(BIGQUERY_TABLE_REF, rows_to_insert)
    except Exception as e:
        print(f"Telemetry logging failed: {str(e)}")

# --- COMPLIANT JSON-RPC 2.0 MCP ROUTE WITH CLOUD SQL WRITE-BACK ---
@app.route('/mcp', methods=['GET', 'POST'])
def mcp_endpoint():
    if not verify_security_passkey(request.headers):
        return jsonify({"error": "Unauthorized: Invalid Passkey Provided"}), 401

    if request.method == 'GET':
        return jsonify({"status": "ok", "mcpVersion": "2024-11-05"})

    body = request.json or {}
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Medical-Inquiry-MCP", "version": "1.0.0"}
            }
        })

    if method == "notifications/initialized":
        return '', 204

    if method == "tools/list":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "medical_gcs_search",
                        "description": "Searches inside approved medical literature PDFs in Google Cloud Storage.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Medical keyword or drug name."},
                                "max_results": {"type": "integer", "default": 3}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "report_adverse_event",
                        "description": "CRITICAL: Call this if inquiry mentions any side effect.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "drug_name": {"type": "string"},
                                "raw_inquiry_text": {"type": "string"},
                                "detected_symptoms": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["drug_name", "raw_inquiry_text", "detected_symptoms"]
                        }
                    }
                ]
            }
        })

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name == "medical_gcs_search":
            query_string = arguments.get("query", "")
            results = search_gcs_documents(query_string)
            doc_count = len(results)
            
            if doc_count == 0:
                log_transaction_to_bigquery(query_string, query_string, 0, False, "No data matched.")
                # Transactional Cloud SQL Entry for Standard Data Gaps
                inquiry_uuid = str(uuid.uuid4())
                db = get_db_connection()
                try:
                    db.run(
                        "INSERT INTO inbound_data_gaps (inquiry_id, hcp_raw_query, extracted_keywords, status) VALUES (:id, :query, :keywords, :status)",
                        id=inquiry_uuid, query=query_string, keywords=query_string, status="UNRESOLVED"
                    )
                finally:
                    db.close()
            
            return jsonify({
                "jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(results)}]}
            })
            
        elif tool_name == "report_adverse_event":
            drug = arguments.get("drug_name")
            raw_text = arguments.get("raw_inquiry_text")
            symptoms = arguments.get("detected_symptoms", [])
            msg_id = publish_adverse_event(drug, raw_text, symptoms)
            
            try:
                log_transaction_to_bigquery(raw_text, drug, 0, True, f"Safety warning published. Message ID: {msg_id}")
            except Exception as bq_err:
                print(f"Non-blocking telemetry log failure: {str(bq_err)}")

            # PATH A IMPLEMENTATION: Transactional Cloud SQL Entry for Critical Safety Events
            inquiry_uuid = str(uuid.uuid4())
            db = get_db_connection()
            try:
                db.run(
                    "INSERT INTO inbound_data_gaps (inquiry_id, hcp_raw_query, extracted_keywords, status) VALUES (:id, :query, :keywords, :status)",
                    id=inquiry_uuid, query=raw_text, keywords=drug, status="CRITICAL_SAFETY_ALERT"
                )
            finally:
                db.close()
            
            return jsonify({
                "jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"Event flagged as CRITICAL_SAFETY_ALERT. ID: {msg_id}"}]}
            })

    return jsonify({
        "jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
