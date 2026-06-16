# test_pinecone_direct.py
import os
from pinecone import Pinecone
from llama_index.embeddings.vertex import VertexTextEmbedding
import google.auth

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

# 1. Initialize the embedding engine locally to convert text strings to vectors
print("🤖 Initializing Vertex AI Text Embedding Model...")
embed_model = VertexTextEmbedding(
    model_name="text-embedding-004",
    project="medical-inquiry-agent",
    location="us-central1",
    credentials=credentials
)

# 2. Establish direct link to the Pinecone Serverless matrix endpoints
pc = Pinecone(api_key=pinecone_api_key)
pinecone_index = pc.Index(index_name)

print("\n--- 🔍 Running Raw In-Memory Vector Similarity Match ---")
search_phrase = "What is the standard approved storage temperature protocol for adult dosages of Xenotrin?"

try:
    # Convert your query string phrase into a raw 768-dimension dense vector array
    query_vector = embed_model.get_query_embedding(search_phrase)
    
    # Query your raw Pinecone index partition namespace directly, forcing it to return text metadata
    raw_response = pinecone_index.query(
        namespace="pharma-medical",
        vector=query_vector,
        top_k=1,
        include_metadata=True
    )
    
    print(f"\nQuery Executed! Matches found in namespace 'pharma-medical': {len(raw_response.matches)}")
    print("-" * 80)
    
    if raw_response.matches:
        for idx, match in enumerate(raw_response.matches):
            print(f"🎯 VECTOR HITCH SECURED [{idx+1}]")
            print(f"🆔 Node ID Signature: {match.id}")
            print(f"📊 Vector Proximity Score (Cosine): {match.score}")
            
            # Extract and print the metadata payload dictionary
            metadata = match.metadata if match.metadata else {}
            print(f"📄 Source File Name: {metadata.get('file_name', 'Not Found')}")
            
            # Print the text snippet sitting inside that single vector chunk
            text_payload = metadata.get("text") or metadata.get("_node_content") or "No text metadata found in this chunk."
            print(f"📋 Raw Content Fragment:\n\"{str(text_payload)[:400]}...\"")
            print("-" * 80)
    else:
        print("📭 Database returned zero matching vectors for this search concept area.")
        
except Exception as e:
    print(f"❌ Structural Execution Error: {str(e)}")
