import os
import io
import json
from flask import Flask, request, jsonify
from google.cloud import storage, pubsub_v1
from pypdf import PdfReader

app = Flask(__name__)

# Initialize Google Cloud Clients
storage_client = storage.Client()
pubsub_client = pubsub_v1.PublisherClient()

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "pharma-medical-reference-docs")
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
TOPIC_ID = os.getenv("GCP_PUBSUB_TOPIC", "pharmacovigilance-alerts")

def verify_security_passkey(request_headers):
    """Validates an explicit custom API header passed securely from Anthropic."""
    auth_header = request_headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
        
    parts = auth_header.split(" ")
    if len(parts) != 2:
        return False
        
    extracted_token = parts[1] # Extract the exact token string
    expected_secret = os.getenv("ANTHROPIC_MCP_SECRET", "PharmaSecretPasskey2026!")
    return extracted_token == expected_secret

# --- ROBUST TEXT & PDF RETRIEVAL LOGIC ---
def search_gcs_documents(query: str, max_results: int = 3):
    """Scans GCS files by splitting long queries into individual keyword tokens."""
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs()
    matched_chunks = []
    
    # Split incoming long strings (e.g. "Xenotrin storage temperature") into individual keywords
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
                
            if file_text:
                # ALL words in the query must be present in the document text (AND logic)
                if all(word in file_text for word in query_words):
                    matched_chunks.append({
                        "source": f"gs://{BUCKET_NAME}/{blob.name}",
                        "text": file_text[:1500]  # Grab the relevant text window
                    })
                    
        except Exception as e:
            print(f"Error parsing file {blob.name}: {str(e)}")
            
        if len(matched_chunks) >= max_results:
            break
            
    return matched_chunks

# --- OFFICIAL JSON-RPC 2.0 MCP ROUTE ---
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
            query = arguments.get("query")
            results = search_gcs_documents(query)
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
