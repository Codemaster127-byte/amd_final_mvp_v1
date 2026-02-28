# Intent Protocol Layer — MVP

> Runtime semantic bridging engine for autonomous agent workflows.  
> Converts software interfaces into executable intent graphs.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Run the demo (no browser needed)

```bash
python demo.py
```

This simulates a full pipeline:
- **Record** → simulated browser session
- **Canonicalize** → normalize raw events
- **Mine** → build probabilistic intent graph
- **Plan** → compute execution path
- **Self-heal** → demonstrate adaptive recovery

### 3. Start the API server

```bash
uvicorn api.main:app --reload --port 8000
```

Then open `http://localhost:8000/docs` for interactive Swagger UI.

---

## API Endpoints

### `POST /workflows/simulate`
Generate a workflow from a built-in scenario (no recording needed).

```bash
curl -X POST http://localhost:8000/workflows/simulate \
  -H "Content-Type: application/json" \
  -d '{"workflow_name": "login_flow", "scenario": "login", "base_url": "https://yourapp.com"}'
```

### `POST /workflows/mine`
Ingest raw browser events and build an intent graph.

```bash
curl -X POST http://localhost:8000/workflows/mine \
  -H "Content-Type: application/json" \
  -d '{"workflow_name": "my_workflow", "raw_events": [...]}'
```

### `GET /workflows`
List all known workflows and their graph structure.

### `POST /execute`
Execute a named intent (dry_run=true for safe planning-only mode).

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_name": "login_flow",
    "intent_label": "dashboard",
    "start_url": "https://yourapp.com/login",
    "dry_run": true
  }'
```

### `POST /feedback`
Record execution outcome to update transition probabilities (self-healing).

```bash
curl -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"workflow_name": "login_flow", "edge_id": "abc123", "success": false}'
```

---

## Architecture

```
Browser Events
     │
     ▼
EventCanonicalizer     ← normalizes selectors, DOM hashes, text labels
     │
     ▼
WorkflowMiner          ← builds probabilistic state machine (IntentGraph)
     │
     ▼
IntentGraph            ← nodes=states, edges=transitions with probabilities
     │
     ▼
IntentExecutor         ← plans + executes path via Playwright
     │
     ├─ Strategy 1: text_match    (resilient to selector drift)
     ├─ Strategy 2: role_match    (ARIA-based, most robust)
     ├─ Strategy 3: selector      (fastest, brittle)
     └─ Strategy 4: network       (bypass UI entirely)
          │
          ▼
     Feedback Loop     ← updates probabilities, enables self-healing
```

## Project Structure

```
intent-protocol-mvp/
├── src/
│   ├── canonicalizer.py    # Event normalization layer
│   ├── graph.py            # Intent graph + workflow miner
│   ├── recorder.py         # Playwright browser recorder
│   └── executor.py         # Multi-strategy execution engine
├── api/
│   └── main.py             # FastAPI REST interface
├── data/                   # Persisted intent graphs (auto-created)
├── demo.py                 # End-to-end demo script
└── requirements.txt
```

## AMD Integration Notes

- **ROCm acceleration**: Replace `torch.device("cpu")` with `torch.device("cuda")` 
  once PyTorch-ROCm is installed for local GPU inference
- **Ryzen CPUs**: Playwright browser orchestration + FastAPI run efficiently on 
  Ryzen multi-core; set `PLAYWRIGHT_WORKERS=N` to match core count
- **Local execution**: All inference runs locally by default — no external API calls

## Next Steps (Beyond MVP)

- [ ] Vector embeddings for semantic state similarity (sentence-transformers)
- [ ] Real ML-based intent clustering (replace text-match heuristics)
- [ ] Graph database backend (Neo4j) for large workflow libraries
- [ ] Multi-session learning with session clustering
- [ ] Visual dashboard for intent graph inspection
- [ ] Desktop app support (Electron/native automation)
