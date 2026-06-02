import os
import io
import json
import uuid
import datetime
from flask import Flask, request, jsonify
from google.cloud import storage, pubsub_v1, bigquery
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

def verify_security_passkey(request_headers):
    auth_header = request_headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    parts = auth_header.split(" ")
    if len(parts) != 2:
        return False
    return parts[1] == os.getenv("ANTHROPIC_MCP_SECRET", "PharmaSecretPasskey2026!")

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
            print(f"CRITICAL ERROR: Exception parsing file {blob.name}. Details: {str(e)}")
            
        if len(matched_chunks) >= max_results:
            break
    return matched_chunks

def publish_adverse_event(drug_name: str, raw_text: str, symptoms: list):
    topic_path = pubsub_client.topic_path(PROJECT_ID, TOPIC_ID)
    payload = {
        "event_type": "ADVERSE_EVENT_FLAG",
        "drug": drug_name,
        "flagged_symptoms": symptoms,
        "original_message": raw_text
    }
    data = json.dumps(payload).encode("utf-8")
    future = pubsub_client.publish(topic_path, data)
    return future.result()

# --- NEW: BIGQUERY TELEMETRY STREAMING LAYER ---
def log_transaction_to_bigquery(raw_query, keywords, doc_count, pv_triggered, draft=""):
    """Streams transaction payloads directly into your BigQuery dataset."""
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
        errors = bq_client.insert_rows_json(BIGQUERY_TABLE_REF, rows_to_insert)
        if errors:
            print(f"BigQuery Insert Errors Detected: {errors}")
    except Exception as e:
        print(f"Asynchronous telemetry recording failed: {str(e)}")

# --- COMPLIANT JSON-RPC 2.0 ROUTING WITH TELEMETRY INTERCEPT ---
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
            
            # AUTOMATED TRACKING INTERCEPT
            # If the database returns 0 results, we log the details to BigQuery immediately
            if doc_count == 0:
                print(f"DATA GAP ALERT: Query '{query_string}' returned zero matching files. Streaming event to BigQuery.")
                log_transaction_to_bigquery(
                    raw_query=query_string,
                    keywords=query_string,
                    doc_count=0,
                    pv_triggered=False,
                    draft="No internal data matched. Systematic grounding fallback triggered."
                )
            
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(results)}]
                }
            })
            
        elif tool_name == "report_adverse_event":
            drug = arguments.get("drug_name")
            raw_text = arguments.get("raw_inquiry_text")
            symptoms = arguments.get("detected_symptoms", [])
            msg_id = publish_adverse_event(drug, raw_text, symptoms)
            
            # Log the safety alert event directly to BigQuery for metrics tracking
            log_transaction_to_bigquery(
                raw_query=raw_text,
                keywords=drug,
                doc_count=0,
                pv_triggered=True,
                draft=f"Safety warning sent to Pub/Sub. Message ID: {msg_id}"
            )
            
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Event submitted successfully. ID: {msg_id}"}]
                }
            })

    return jsonify({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"}
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
