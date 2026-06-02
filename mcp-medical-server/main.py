import os
import json
import urllib.request
from flask import Flask, request, jsonify
from google.cloud import storage, pubsub_v1

app = Flask(__name__)

storage_client = storage.Client()
pubsub_client = pubsub_v1.PublisherClient()

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "pharma-medical-reference-docs")
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
TOPIC_ID = os.getenv("GCP_PUBSUB_TOPIC", "pharmacovigilance-alerts")

# --- SIMPLIFIED PRODUCTION DEVELOPMENT GATEWAY ---
def verify_security_passkey(request_headers):
    """Validates an explicit custom API header passed securely from Anthropic."""
    auth_header = request_headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
        
    extracted_token = auth_header.split(" ")[1]
    
    # We match this against an environment variable stored safely in Cloud Run
    expected_secret = os.getenv("ANTHROPIC_MCP_SECRET", "PharmaSecretPasskey2026!")
    return extracted_token == expected_secret


# --- CORE BUSINESS LOGIC ---
def search_gcs_documents(query: str, max_results: int = 3):
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs(max_results=10)
    matched_chunks = []
    
    for blob in blobs:
        if blob.name.endswith('.txt') or blob.name.endswith('.pdf'):
            try:
                content = blob.download_as_text(errors='ignore')
                if query.lower() in content.lower():
                    matched_chunks.append({
                        "source": f"gs://{BUCKET_NAME}/{blob.name}",
                        "text": content[:1500]
                    })
            except Exception:
                pass
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

# --- OFFICIAL SINGLE MCP ROUTE ---
@app.route('/mcp', methods=['GET', 'POST'])
def mcp_endpoint():
    """Unified entrypoint matching the Streamable HTTP MCP specification."""
    
    # 1. Enforce custom security
    if not verify_security_passkey(request.headers):
        return jsonify({"error": "Unauthorized: Invalid Passkey Provided"}), 401

    # 2. GET = simple health/discovery check
    if request.method == 'GET':
        return jsonify({"status": "ok", "mcpVersion": "2024-11-05"})

    # 3. All POST requests are JSON-RPC 2.0
    body = request.json or {}
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    # STEP 1 OF HANDSHAKE: initialize
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

    # STEP 2 OF HANDSHAKE: initialized notification (no response needed)
    if method == "notifications/initialized":
        return '', 204

    # STEP 3: Tool list
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

    # STEP 4: Tool execution
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

    # Unknown method
    return jsonify({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"}
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
