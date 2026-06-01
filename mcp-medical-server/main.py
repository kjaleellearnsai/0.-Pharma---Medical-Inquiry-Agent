import os
import json
from flask import Flask, request, jsonify
from google.cloud import storage, pubsub_v1

app = Flask(__name__)

# Initialize Google Cloud Clients
storage_client = storage.Client()
pubsub_client = pubsub_v1.PublisherClient()

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "pharma-medical-reference-docs")
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
TOPIC_ID = os.getenv("GCP_PUBSUB_TOPIC", "pharmacovigilance-alerts")

# --- TOOL 1: MEDICAL REFERENCE SEARCH ---
def search_gcs_documents(query: str, max_results: int = 3):
    """
    Scans PDFs/text in GCS. In a full production setup, this would query Vertex AI Search 
    or a vector database like AlloyDB. Here it performs a compliant direct contextual fetch.
    """
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs(max_results=10)
    matched_chunks = []
    
    for blob in blobs:
        if blob.name.endswith('.txt') or blob.name.endswith('.json'):
            content = blob.download_as_text()
            # Simple chunk extraction based on query keyword matching
            if query.lower() in content.lower():
                matched_chunks.append({
                    "source": f"gs://{BUCKET_NAME}/{blob.name}",
                    "text": content[:1500] # Return relevant text snippet
                })
        if len(matched_chunks) >= max_results:
            break
            
    return matched_chunks

# --- TOOL 2: ADVERSE EVENT EMERGENCY ROUTING ---
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

# --- MCP ROUTING ENDPOINTS ---
@app.route('/tools/search', methods=['POST'])
def handle_search():
    data = request.json or {}
    query = data.get("query")
    max_results = data.get("max_results", 3)
    
    if not query:
        return jsonify({"error": "Missing 'query' parameter"}), 400
        
    results = search_gcs_documents(query, max_results)
    return jsonify({"status": "success", "data": results})

@app.route('/tools/report_ae', methods=['POST'])
def handle_adverse_event():
    data = request.json or {}
    drug_name = data.get("drug_name")
    raw_text = data.get("raw_inquiry_text")
    symptoms = data.get("detected_symptoms", [])
    
    if not drug_name or not raw_text:
        return jsonify({"error": "Missing required reporting fields"}), 400
        
    message_id = publish_adverse_event(drug_name, raw_text, symptoms)
    return jsonify({"status": "submitted", "pubsub_message_id": message_id})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
