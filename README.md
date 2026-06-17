# Medical Inquiry Agent

An AI-assisted medical affairs platform for handling healthcare professional (HCP) inquiries about pharmaceutical products. The system combines semantic search over approved reference literature, automated pharmacovigilance routing, and a human-in-the-loop dashboard for Medical Science Liaisons (MSLs).

Built for regulated environments where answers must be grounded in approved documentation, adverse events must be escalated immediately, and every interaction requires an audit trail.

## Overview

When an HCP asks a question—through an AI agent connected via MCP—the system:

1. **Searches approved literature** using semantic vector retrieval (OmniRAG over Pinecone + Vertex AI embeddings)
2. **Flags adverse events** and publishes safety alerts to Google Pub/Sub for pharmacovigilance teams
3. **Logs every transaction** to BigQuery and Cloud SQL for compliance and review
4. **Surfaces unresolved cases** in a Streamlit dashboard where MSLs can resolve data gaps manually

## Architecture

### System Architecture

High-level view of components, integrations, and data paths across the platform.

```mermaid
flowchart TB
    subgraph Clients["Clients"]
        HCP["Healthcare Professional"]
        Agent["AI Agent<br/>(MCP Client e.g. Claude)"]
    end

    subgraph Apps["Application Services"]
        MCP["MCP Medical Server<br/>Flask · main.py<br/>POST /mcp"]
        Dashboard["MSL Dashboard<br/>Streamlit · app.py"]
    end

    subgraph RAG["OmniRAG Retrieval"]
        Vertex["Vertex AI Embeddings<br/>text-embedding-004"]
        Pinecone["Pinecone Vector Index<br/>omni-rag-platform-index<br/>namespace: pharma-medical"]
    end

    subgraph GCPData["Google Cloud Data & Messaging"]
        GCS["Cloud Storage<br/>pharma-medical-reference-docs"]
        CloudSQL[("Cloud SQL PostgreSQL<br/>inbound_data_gaps<br/>msl_resolutions")]
        BQ[("BigQuery<br/>telemetry_data.agent_logs")]
        PubSub["Pub/Sub<br/>pharmacovigilance-alerts"]
    end

    subgraph Teams["Human Teams"]
        MSL["Medical Science Liaison"]
        PV["Pharmacovigilance"]
    end

    HCP -->|"asks medical question"| Agent
    Agent -->|"JSON-RPC 2.0 + Bearer token"| MCP

    MCP -->|"embed query"| Vertex
    Vertex -->|"query vector"| Pinecone
    Pinecone -->|"matched chunks ≥ 0.72 score"| MCP
    MCP -->|"tool results"| Agent
    Agent -->|"draft response"| HCP

    GCS -.->|"reference docs ingested into"| Pinecone

    MCP -->|"every inquiry"| BQ
    MCP -->|"queue unresolved / safety cases"| CloudSQL
    MCP -->|"report_adverse_event"| PubSub
    PubSub --> PV

    MSL -->|"browser"| Dashboard
    Dashboard -->|"read queue · write resolutions"| CloudSQL
```

### Inquiry Flow

End-to-end sequence for a literature search and an adverse-event escalation.

```mermaid
sequenceDiagram
    autonumber
    participant HCP as Healthcare Professional
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Vertex as Vertex AI
    participant PC as Pinecone
    participant BQ as BigQuery
    participant SQL as Cloud SQL
    participant PS as Pub/Sub
    participant MSL as MSL Dashboard

    HCP->>Agent: Submit medical inquiry

    alt Literature search (medical_gcs_search)
        Agent->>MCP: tools/call · medical_gcs_search
        MCP->>Vertex: Generate query embedding
        Vertex-->>MCP: Query vector
        MCP->>PC: Semantic search (top_k=4)
        PC-->>MCP: Matched chunks + scores

        alt Score ≥ 0.72 threshold
            MCP->>BQ: Log RESOLVED_BY_AI
            MCP->>SQL: Insert RESOLVED_BY_AI
            MCP-->>Agent: Approved literature excerpts
            Agent-->>HCP: Grounded draft response
        else Score below threshold
            MCP->>BQ: Log UNRESOLVED
            MCP->>SQL: Insert UNRESOLVED (data gap)
            MCP-->>Agent: Empty result set
            Agent-->>HCP: Escalate / defer response
            MSL->>SQL: Review queue via dashboard
            MSL->>SQL: RESOLVED_BY_HITL override
        end

    else Adverse event (report_adverse_event)
        Agent->>MCP: tools/call · report_adverse_event
        MCP->>PS: Publish ADVERSE_EVENT_FLAG
        MCP->>BQ: Log pv_triggered=true
        MCP->>SQL: Insert CRITICAL_SAFETY_ALERT
        MCP-->>Agent: Safety alert confirmation
        Agent-->>HCP: Acknowledge safety routing
    end
```

### Inquiry Status State Machine

```mermaid
stateDiagram-v2
    [*] --> RESOLVED_BY_AI: Semantic match found
    [*] --> UNRESOLVED: No match above threshold
    [*] --> CRITICAL_SAFETY_ALERT: Adverse event reported

    UNRESOLVED --> RESOLVED_BY_HITL: MSL manual override
    CRITICAL_SAFETY_ALERT --> RESOLVED_BY_HITL: MSL archives after PV routing

    RESOLVED_BY_AI --> [*]
    RESOLVED_BY_HITL --> [*]
```

### Deployment Architecture

How services are containerized and deployed across GCP and external providers.

```mermaid
flowchart TB
    subgraph Internet["Public / Client Access"]
        MCPClient["MCP Client<br/>(AI Agent)"]
        MSLBrowser["MSL Browser"]
    end

    subgraph GCP["Google Cloud Platform · medical-inquiry-agent · us-central1"]
        subgraph CloudRun["Cloud Run Services"]
            CR_MCP["Service: medical-inquiry-mcp<br/>Image: Dockerfile<br/>Port 8080 · main.py"]
            CR_Dash["Service: medical-inquiry-dashboard<br/>Image: Dockerfile.dashboard<br/>Port 8080 · app.py"]
        end

        subgraph Managed["Managed GCP Services"]
            CloudSQL[("Cloud SQL<br/>pharma-dashboard-db<br/>PostgreSQL 5432")]
            Vertex["Vertex AI<br/>Embedding API"]
            GCS["Cloud Storage<br/>Reference document bucket"]
            BQ[("BigQuery<br/>agent_logs table")]
            PubSub["Pub/Sub Topic<br/>pharmacovigilance-alerts"]
        end

        SA["Service Account<br/>GCP_SERVICE_ACCOUNT_JSON"]
    end

    subgraph External["External SaaS"]
        Pinecone["Pinecone<br/>omni-rag-platform-index"]
    end

    subgraph LocalDev["Local Development (optional)"]
        LocalMCP["python main.py :8080"]
        LocalDash["streamlit run app.py"]
        SQLProxy["Cloud SQL Auth Proxy<br/>127.0.0.1:5432"]
        EnvFile[".env / gcp-key.json"]
    end

    MCPClient -->|"HTTPS · Bearer auth"| CR_MCP
    MSLBrowser -->|"HTTPS"| CR_Dash

    CR_MCP <-->|"Unix socket /cloudsql/…"| CloudSQL
    CR_Dash <-->|"Unix socket /cloudsql/…"| CloudSQL

    CR_MCP --> Vertex
    CR_MCP --> GCS
    CR_MCP --> BQ
    CR_MCP --> PubSub
    CR_MCP -->|"PINECONE_API_KEY"| Pinecone

    SA -.->|"credentials"| CR_MCP
    SA -.->|"credentials"| CR_Dash

    EnvFile -.-> LocalMCP
    EnvFile -.-> LocalDash
    LocalMCP --> Pinecone
    LocalMCP --> Vertex
    LocalDash --> SQLProxy
    SQLProxy --> CloudSQL
```

**Production topology notes:**

| Component | Deploy target | Connection |
|-----------|---------------|------------|
| MCP Server | Cloud Run (`Dockerfile`) | Cloud SQL via `/cloudsql` socket; Pinecone over HTTPS |
| MSL Dashboard | Cloud Run (`Dockerfile.dashboard`) | Cloud SQL via `/cloudsql` socket |
| Reference docs | GCS bucket → Pinecone index | Ingested outside the request path |
| Audit telemetry | BigQuery + Cloud SQL | Written on every MCP tool call |
| PV alerts | Pub/Sub | Triggered by `report_adverse_event` |

### CI/CD Pipeline

Recommended build-and-deploy pipeline for the two Cloud Run services. The repository includes a starter workflow at [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

```mermaid
flowchart LR
    subgraph Source["Source Control"]
        Dev["Developer commit"]
        Git["Git repository"]
        PR["Pull request / main branch"]
    end

    subgraph CI["Continuous Integration"]
        Checkout["Checkout code"]
        Test["Unit & integration tests<br/>test_pinecone_direct.py"]
        Lint["Dependency audit"]
        BuildMCP["docker build -f Dockerfile"]
        BuildDash["docker build -f Dockerfile.dashboard"]
        Scan["Container vulnerability scan"]
    end

    subgraph Registry["Google Artifact Registry"]
        ImgMCP["medical-inquiry-mcp:sha"]
        ImgDash["medical-inquiry-dashboard:sha"]
    end

    subgraph CD["Continuous Deployment"]
        DeployMCP["gcloud run deploy<br/>medical-inquiry-mcp"]
        DeployDash["gcloud run deploy<br/>medical-inquiry-dashboard"]
        Config["Inject env vars & secrets<br/>Cloud Run revision"]
        Smoke["Post-deploy smoke test<br/>GET /mcp · dashboard load"]
        Rollback["Rollback on failure"]
    end

    subgraph Prod["Production"]
        CR_MCP["Cloud Run · MCP Server"]
        CR_Dash["Cloud Run · MSL Dashboard"]
    end

    Dev --> Git --> PR
    PR --> Checkout --> Test --> Lint
    Lint --> BuildMCP --> Scan --> ImgMCP
    Lint --> BuildDash --> ImgDash
    ImgMCP --> DeployMCP --> Config
    ImgDash --> DeployDash --> Config
    Config --> Smoke
    Smoke -->|pass| CR_MCP
    Smoke -->|pass| CR_Dash
    Smoke -->|fail| Rollback
    Rollback -.-> ImgMCP
```

**Pipeline stages:**

| Stage | Action | Artifact / outcome |
|-------|--------|-------------------|
| Build | `docker build` from `mcp-medical-server/` | Two container images (MCP + dashboard) |
| Publish | Push to Artifact Registry | Immutable image tags per commit SHA |
| Deploy | `gcloud run deploy` with Cloud SQL connector | New Cloud Run revisions in `us-central1` |
| Verify | Hit `GET /mcp` with bearer token; load Streamlit UI | Confirms auth, DB socket, and external API reachability |
| Promote | Tag `:latest` or environment-specific tag after smoke pass | Stable rollback pointer |

**Suggested automation:** GitHub Actions or Cloud Build triggered on merge to `main`, with secrets sourced from GCP Secret Manager rather than committed `.env` files.

#### GitHub Actions setup

The [`deploy.yml`](.github/workflows/deploy.yml) workflow runs on every pull request (CI only) and on merges to `main` (full build → deploy → smoke test → rollback on failure).

**One-time GCP prerequisites:**

1. Create an Artifact Registry repository: `medical-inquiry-agent` in `us-central1`
2. Create Secret Manager secrets (names must match the workflow):
   - `PINECONE_API_KEY`
   - `DB_PASSWORD`
   - `ANTHROPIC_MCP_SECRET`
   - `GCP_SERVICE_ACCOUNT_JSON` (runtime service account key for the MCP server)
3. Grant the deploy service account:
   - `roles/run.admin`, `roles/artifactregistry.writer`, `roles/secretmanager.secretAccessor`
   - `roles/cloudsql.client` (Cloud SQL connector on Cloud Run)
4. Configure [Workload Identity Federation](https://github.com/google-github-actions/auth#setting-up-workload-identity-federation) between GitHub and GCP

**Required GitHub repository secrets:**

| Secret | Purpose |
|--------|---------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | WIF provider resource name |
| `GCP_SERVICE_ACCOUNT_EMAIL` | Deploy service account email |
| `ANTHROPIC_MCP_SECRET` | Bearer token used by the post-deploy MCP smoke test |

**Workflow behavior:**

| Trigger | Jobs |
|---------|------|
| Pull request → `main` | `test` — compile sources + dependency audit |
| Push → `main` | `test` → `build-and-deploy` — push images, deploy both Cloud Run services, smoke test, rollback on failure |
| Manual (`workflow_dispatch`) | Same as push to `main` |

Override defaults (project ID, region, service names) via the `env` block at the top of `deploy.yml`.

### Network & Security Boundaries

Trust zones, authentication layers, and how secrets flow into runtime services.

```mermaid
flowchart TB
    subgraph Untrusted["Untrusted Zone · Public Internet"]
        Agent["MCP Client / AI Agent"]
        Browser["MSL Browser"]
        PineconeExt["Pinecone SaaS"]
    end

    subgraph Edge["Edge Layer · TLS & Application Auth"]
        TLS["TLS 1.2+ termination<br/>Cloud Run managed HTTPS"]
        Bearer["Application auth gate<br/>Authorization: Bearer token<br/>ANTHROPIC_MCP_SECRET"]
        IAP["Optional: Identity-Aware Proxy<br/>for MSL Dashboard"]
    end

    subgraph GCPBoundary["GCP Project Trust Boundary · medical-inquiry-agent"]
        subgraph Compute["Compute Tier"]
            CR_MCP["Cloud Run · MCP Server<br/>main.py"]
            CR_Dash["Cloud Run · MSL Dashboard<br/>app.py"]
        end

        subgraph Secrets["Secrets & Config"]
            SM["Secret Manager<br/>DB_PASSWORD<br/>PINECONE_API_KEY<br/>ANTHROPIC_MCP_SECRET"]
            EnvRun["Cloud Run env vars<br/>GCP_PROJECT_ID<br/>PINECONE_INDEX_NAME"]
            SAKey["Service account JSON<br/>GCP_SERVICE_ACCOUNT_JSON"]
        end

        subgraph IAMLayer["IAM · Least Privilege"]
            SA["Runtime service account"]
            Roles["Roles: Cloud SQL Client<br/>Vertex AI User · BigQuery Data Editor<br/>Pub/Sub Publisher · Storage Object Viewer"]
        end

        subgraph DataPlane["Data Plane · Private / IAM-gated"]
            CloudSQL[("Cloud SQL PostgreSQL<br/>Private via unix socket<br/>/cloudsql/…")]
            Vertex["Vertex AI API"]
            BQ[("BigQuery")]
            PubSub["Pub/Sub"]
            GCS["Cloud Storage"]
        end
    end

    subgraph LocalBoundary["Local Dev Boundary · Non-production"]
        DotEnv[".env file · gitignored"]
        KeyFile["gcp-key.json · gitignored"]
        SQLProxy["Cloud SQL Auth Proxy<br/>127.0.0.1:5432"]
    end

    Agent -->|"HTTPS"| TLS
    Browser -->|"HTTPS"| TLS
    TLS --> Bearer --> CR_MCP
    TLS --> IAP --> CR_Dash

    SM -.->|"mounted at deploy"| CR_MCP
    SM -.->|"mounted at deploy"| CR_Dash
    EnvRun -.-> CR_MCP
    SAKey -.-> SA
    SA --> Roles
    Roles -.->|"OAuth2 token"| CR_MCP
    Roles -.->|"OAuth2 token"| CR_Dash

    CR_MCP -->|"unix socket · no public IP"| CloudSQL
    CR_Dash -->|"unix socket · no public IP"| CloudSQL
    CR_MCP -->|"IAM-scoped API calls"| Vertex
    CR_MCP --> BQ
    CR_MCP --> PubSub
    CR_MCP --> GCS
    CR_MCP -->|"API key over TLS"| PineconeExt

    DotEnv -.-> SQLProxy
    KeyFile -.-> SQLProxy
    SQLProxy -->|"encrypted tunnel"| CloudSQL
```

**Security controls mapped to code:**

| Control | Implementation | Location |
|---------|----------------|----------|
| MCP endpoint auth | Bearer token validated on every request | `verify_security_passkey()` in `main.py` |
| GCP identity | Service account JSON, `gcp-key.json`, or Application Default Credentials | `main.py` startup |
| Database access | Cloud SQL unix socket in Cloud Run; proxy locally | `get_db_connection()` in `main.py` / `app.py` |
| Retrieval safety | Cosine similarity threshold ≥ 0.72 | `search_omnirag_vector_matrix()` |
| Secret hygiene | `.env`, `gcp-key.json` gitignored | `.gitignore` |
| Audit immutability | Every tool call logged to BigQuery + Cloud SQL | `log_transaction_to_bigquery()`, DB inserts |

**Network posture notes:**

- Cloud Run services accept inbound HTTPS only; Cloud SQL is not exposed on a public IP when using the Cloud SQL connector.
- Pinecone is the only external data-plane dependency outside GCP; traffic is outbound HTTPS with an API key.
- Production secrets should live in **Secret Manager** and be referenced by Cloud Run at deploy time—not baked into images or committed to Git.
- Consider **Identity-Aware Proxy (IAP)** on the Streamlit dashboard so only authenticated MSL staff reach the operational workbench.

## Features

### MCP Server (`mcp-medical-server/main.py`)

Exposes two tools to connected AI agents:

| Tool | Purpose |
|------|---------|
| `medical_gcs_search` | Semantic search over approved medical reference documents stored in Pinecone |
| `report_adverse_event` | Escalates suspected adverse events to pharmacovigilance via Pub/Sub |

**Safety guardrails:**
- Vector similarity threshold (0.72 cosine score) prevents low-confidence answers from being returned
- Unresolved queries are logged as data gaps for human review
- Bearer token authentication protects the MCP endpoint

### MSL Dashboard (`mcp-medical-server/app.py`)

A Streamlit application for the medical affairs team:

- **Active Action Queue** — Outstanding data gaps and critical safety alerts requiring attention
- **Full Historical Audit Log** — Immutable record of all inquiries and resolutions
- **Manual Override Workbench** — MSLs can paste verified reference literature and approve compliant responses for unresolved inquiries

### Reference Documentation

Sample approved product literature lives in `Reference docs/` (e.g., Dupixent, Xenotrin). These documents are intended to be ingested into the Pinecone vector index for retrieval.

## Tech Stack

| Layer | Technology |
|-------|------------|
| MCP Protocol | JSON-RPC 2.0 over HTTP (Flask) |
| Vector Search | Pinecone + LlamaIndex |
| Embeddings | Google Vertex AI (`text-embedding-004`) |
| Storage | Google Cloud Storage |
| Messaging | Google Cloud Pub/Sub |
| Analytics | Google BigQuery |
| Database | Cloud SQL (PostgreSQL) |
| Dashboard | Streamlit |
| Deployment | Docker → Google Cloud Run |

## Project Structure

```
.
├── mcp-medical-server/
│   ├── main.py                  # MCP server (Flask)
│   ├── app.py                   # MSL dashboard (Streamlit)
│   ├── requirements.txt         # MCP server dependencies
│   ├── requirements-dashboard.txt
│   ├── Dockerfile               # MCP server container
│   ├── Dockerfile.dashboard     # Dashboard container
│   ├── print_my_vector.py       # Vector debugging utility
│   └── test_pinecone_direct.py  # Pinecone connectivity test
├── Reference docs/              # Approved product reference literature
├── .github/workflows/
│   └── deploy.yml               # CI/CD: build, deploy, smoke test
├── .env.example                 # Environment variable template
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.11+
- Google Cloud project with Vertex AI, Cloud SQL, BigQuery, Pub/Sub, and Cloud Storage enabled
- Pinecone account with an index populated from your reference documents
- (Optional) Cloud SQL Auth Proxy for local database access

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `PINECONE_API_KEY` | Pinecone API key |
| `PINECONE_INDEX_NAME` | Vector index name (default: `omni-rag-platform-index`) |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID |
| `GCP_PROJECT_ID` | Same as above (used by server) |
| `GCP_SERVICE_ACCOUNT_JSON` | Service account credentials (JSON or base64-encoded) |
| `GCS_BUCKET_NAME` | GCS bucket for reference documents |
| `GCP_PUBSUB_TOPIC` | Pub/Sub topic for pharmacovigilance alerts |
| `ANTHROPIC_MCP_SECRET` | Bearer token for MCP endpoint authentication |
| `DB_PASSWORD` | Cloud SQL PostgreSQL password |
| `EMBED_MODEL_NAME` | Vertex embedding model (default: `text-embedding-004`) |

For local development, you can also place a `gcp-key.json` service account file in the project root (this path is gitignored).

### Run the MCP Server Locally

```bash
cd mcp-medical-server
pip install -r requirements.txt
python main.py
```

The server listens on port `8080` by default. Connect your MCP client to:

```
http://localhost:8080/mcp
```

Include the bearer token in requests:

```
Authorization: Bearer <ANTHROPIC_MCP_SECRET>
```

### Run the Dashboard Locally

Requires a running Cloud SQL Auth Proxy or direct network access to the database:

```bash
cd mcp-medical-server
pip install -r requirements-dashboard.txt
streamlit run app.py
```

### Docker Deployment

**MCP Server:**

```bash
cd mcp-medical-server
docker build -t medical-inquiry-mcp .
docker run -p 8080:8080 --env-file ../.env medical-inquiry-mcp
```

**Dashboard:**

```bash
cd mcp-medical-server
docker build -f Dockerfile.dashboard -t medical-inquiry-dashboard .
docker run -p 8080:8080 medical-inquiry-dashboard
```

Both services are designed to run on Google Cloud Run with Cloud SQL socket connections.

## MCP Tools Reference

### `medical_gcs_search`

Searches approved medical literature using semantic vector retrieval.

**Input:**
```json
{
  "query": "What is the approved adult dosage for Xenotrin?"
}
```

**Output:** JSON array of matched document chunks with source file names and excerpt text. Returns an empty array if no chunks meet the similarity threshold.

### `report_adverse_event`

Flags a suspected adverse event for pharmacovigilance review.

**Input:**
```json
{
  "drug_name": "Xenotrin",
  "raw_inquiry_text": "Patient reported severe nausea after starting Xenotrin 10mg.",
  "detected_symptoms": ["nausea"]
}
```

**Output:** Confirmation with a Pub/Sub message ID. The inquiry is logged as `CRITICAL_SAFETY_ALERT` in the database.

## Inquiry Status Flow

| Status | Meaning |
|--------|---------|
| `RESOLVED_BY_AI` | Semantic search returned matching approved literature |
| `UNRESOLVED` | No sufficiently confident match; queued for MSL review |
| `CRITICAL_SAFETY_ALERT` | Adverse event detected; routed to pharmacovigilance |
| `RESOLVED_BY_HITL` | Manually resolved by an MSL via the dashboard |

## Security Notes

- Never commit `.env`, `gcp-key.json`, or other credential files
- The MCP endpoint requires bearer token authentication
- Adverse event cases are locked in the dashboard—MSLs cannot modify safety alerts, only archive them after PV routing
- All AI-generated responses should be reviewed against approved labeling before dispatch to HCPs

## License

No license file is included. Add one before distributing or open-sourcing this project.
