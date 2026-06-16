# print_my_vector.py
import os
from pinecone import Pinecone
import google.auth
import json

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Explicitly assign your active testing project identity
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "medical-inquiry-agent")
credentials, project = google.auth.default()

pinecone_api_key = os.environ.get("PINECONE_API_KEY", "").strip()
if not pinecone_api_key:
    raise RuntimeError("Set PINECONE_API_KEY in .env or your environment.")

index_name = os.environ.get("PINECONE_INDEX_NAME", "omni-rag-platform-index")

# Connect directly to your Pinecone Serverless matrix endpoints
pc = Pinecone(api_key=pinecone_api_key)
pinecone_index = pc.Index(index_name)

print("--- 🔬 Extracting the Single Vector Chunk Content ---")

try:
    # Query your raw index partition with an empty vector filter to scan for the single asset row
    # We pass a dummy vector of 768 zeroes just to force a raw match response from the workspace
    dummy_vector = [0.0] * 768
    raw_response = pinecone_index.query(
        namespace="pharma-medical",
        vector=dummy_vector,
        top_k=1,
        include_metadata=True
    )
    
    if raw_response.matches:
        match = raw_response.matches[0]
        metadata = match.metadata if match.metadata else {}
        node_content_str = metadata.get("_node_content", "")
        
        if node_content_str:
            node_json = json.loads(node_content_str)
            verbatim_text = node_json.get("text", "No text key inside JSON.")
            
            print("✅ VECTOR CONTENT FOUND!")
            print(f"📄 File Name: {metadata.get('file_name', 'Unknown.pdf')}")
            print(f"📊 Raw Content Payload inside your Database:\n")
            print(f"\" {verbatim_text} \"")
        else:
            print("⚠️ Vector exists but '_node_content' text payload metadata attribute is empty.")
    else:
        print("📭 Namespace partition folder 'pharma-medical' appears to be completely empty.")
        
except Exception as e:
    print(f"❌ Execution Error: {str(e)}")
