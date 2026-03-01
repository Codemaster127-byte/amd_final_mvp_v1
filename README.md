# Intent Protocol Layer (MVP)

This project turns browser actions into a structured workflow graph.

It can:

- Record browser interactions  
- Convert them into a clean internal format  
- Build a state graph from them  
- Plan a path to a target page  
- Execute that plan in a real browser  
- Adjust probabilities based on success or failure  

This is an MVP focused on clarity and correctness.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

---

### 2. Run the demo

```bash
python demo.py
```

The demo shows:

- Raw simulated events  
- Canonicalized events  
- Generated intent graph  
- Execution plan  
- Self-healing behavior  

No real browser interaction is required for the demo.

---

### 3. Start the API

```bash
uvicorn api.main:app --reload --port 8000
```

Then open:

```
http://localhost:8000/docs
```

to test the API using Swagger.

---

## Main API Endpoints

### POST `/workflows/simulate`

Create a workflow graph using a built-in scenario (`login` or `search`).

---

### POST `/workflows/mine`

Send raw browser events to build or update a workflow graph.

---

### GET `/workflows`

List all stored workflows.

---

### POST `/execute`

Execute an intent from a workflow.

Set `"dry_run": true` to generate the plan without launching a browser.

---

### POST `/feedback`

Update transition probabilities based on execution results.

---

## How It Works

1. Browser events are captured  
2. Events are normalized  
3. A directed graph is built:
   - Nodes = UI states  
   - Edges = transitions  
4. The executor finds a path to a target state  
5. If a step fails, it searches for an alternate path  

Simple as that.

---

## Project Structure

```
intent-protocol-mvp/
├── src/
│   ├── canonicalizer.py
│   ├── graph.py
│   ├── recorder.py
│   └── executor.py
├── api/
│   └── main.py
├── data/          # saved workflow graphs
├── demo.py
└── requirements.txt
```

---

## Notes

- Everything runs locally  
- Graphs are saved in the `data/` directory  
- This is a foundation project — designed to be extended  