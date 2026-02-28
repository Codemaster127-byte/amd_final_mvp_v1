"""
Intent Protocol Layer — REST API
Exposes the core workflow recording, mining, and execution capabilities.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.canonicalizer import EventCanonicalizer
from src.graph import WorkflowMiner, IntentGraph
from src.executor import IntentExecutor
from src.recorder import ScriptedRecorder


# ──────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Intent Protocol Layer MVP",
    description="Runtime semantic bridging engine for autonomous agent workflows",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

canonicalizer = EventCanonicalizer()
graphs: dict[str, IntentGraph] = {}


def load_saved_graphs():
    """Load any previously saved graphs from disk."""
    for f in DATA_DIR.glob("*.json"):
        try:
            g = IntentGraph.load(str(f))
            graphs[g.workflow_name] = g
            print(f"  Loaded graph: {g.summary()}")
        except Exception as e:
            print(f"  Warning: could not load {f}: {e}")


load_saved_graphs()


# ──────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────

class MineRequest(BaseModel):
    workflow_name: str
    raw_events: list[dict]


class ExecuteRequest(BaseModel):
    workflow_name: str
    intent_label: str
    start_url: str
    dry_run: bool = True      # Default to dry_run for safety in demos
    headless: bool = True


class FeedbackRequest(BaseModel):
    workflow_name: str
    edge_id: str
    success: bool


class SimulateRequest(BaseModel):
    workflow_name: str
    scenario: str             # "login" or "search"
    base_url: str = "https://example.com"


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "Intent Protocol Layer",
        "version": "0.1.0-mvp",
        "status": "running",
        "loaded_workflows": list(graphs.keys()),
    }


@app.get("/workflows")
def list_workflows():
    """List all known workflows and their graph summaries."""
    return {
        name: {
            "nodes": len(g.nodes),
            "edges": len(g.edges),
            "entry_node": g.entry_node,
            "node_labels": [n.semantic_label for n in g.nodes.values()],
        }
        for name, g in graphs.items()
    }


@app.get("/workflows/{workflow_name}")
def get_workflow(workflow_name: str):
    """Get the full intent graph for a workflow."""
    if workflow_name not in graphs:
        raise HTTPException(404, f"Workflow '{workflow_name}' not found")
    return graphs[workflow_name].to_dict()


@app.post("/workflows/mine")
def mine_workflow(req: MineRequest):
    """
    Ingest raw browser events, canonicalize them, and build/update an intent graph.
    """
    if not req.raw_events:
        raise HTTPException(400, "raw_events must not be empty")

    # Canonicalize events
    canonical_events = canonicalizer.canonicalize_session(req.raw_events)
    if len(canonical_events) < 2:
        raise HTTPException(400, f"Only {len(canonical_events)} valid events found (need ≥2)")

    # Mine or update graph
    miner = WorkflowMiner(req.workflow_name)
    if req.workflow_name in graphs:
        # Merge new session into existing graph
        miner.graph = graphs[req.workflow_name]

    graph = miner.mine_session(canonical_events)
    graphs[req.workflow_name] = graph

    # Persist to disk
    graph.save(str(DATA_DIR / f"{req.workflow_name}.json"))

    return {
        "workflow_name": req.workflow_name,
        "events_processed": len(canonical_events),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "node_labels": [n.semantic_label for n in graph.nodes.values()],
        "message": "Graph built successfully",
    }


@app.post("/workflows/simulate")
async def simulate_workflow(req: SimulateRequest):
    """
    Generate a simulated recording and mine it into a graph.
    Useful for demos without a real browser recording.
    """
    recorder = ScriptedRecorder()

    if req.scenario == "login":
        raw_events = await recorder.simulate_login_workflow(
            req.base_url + "/login"
        )
    elif req.scenario == "search":
        raw_events = await recorder.simulate_search_workflow(
            req.base_url + "/search"
        )
    else:
        raise HTTPException(400, f"Unknown scenario: {req.scenario}. Use 'login' or 'search'")

    # Mine the simulated events
    canonical = canonicalizer.canonicalize_session(raw_events)
    miner = WorkflowMiner(req.workflow_name)
    graph = miner.mine_session(canonical)
    graphs[req.workflow_name] = graph
    graph.save(str(DATA_DIR / f"{req.workflow_name}.json"))

    return {
        "workflow_name": req.workflow_name,
        "scenario": req.scenario,
        "raw_events": len(raw_events),
        "canonical_events": len(canonical),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "node_labels": [n.semantic_label for n in graph.nodes.values()],
        "edges_detail": [
            {
                "from": graph.nodes.get(e.from_node, {}).semantic_label
                    if e.from_node in graph.nodes else e.from_node,
                "action": f"{e.event_type}: {e.text_label}",
                "to": graph.nodes.get(e.to_node, {}).semantic_label
                    if e.to_node in graph.nodes else e.to_node,
                "probability": round(e.probability, 2),
            }
            for e in graph.edges.values()
        ]
    }


@app.post("/execute")
async def execute_intent(req: ExecuteRequest):
    """
    Execute a named intent against a live browser.
    dry_run=true returns the plan without browser interaction.
    """
    if req.workflow_name not in graphs:
        raise HTTPException(404, f"Workflow '{req.workflow_name}' not found. Mine it first.")

    graph = graphs[req.workflow_name]
    executor = IntentExecutor(graph, headless=req.headless)

    trace = await executor.execute_intent(
        intent_label=req.intent_label,
        start_url=req.start_url,
        dry_run=req.dry_run,
    )

    return trace.to_dict()


@app.post("/feedback")
def record_feedback(req: FeedbackRequest):
    """
    Record execution feedback to update transition probabilities.
    This is the self-healing mechanism.
    """
    if req.workflow_name not in graphs:
        raise HTTPException(404, f"Workflow '{req.workflow_name}' not found")

    graph = graphs[req.workflow_name]
    if req.edge_id not in graph.edges:
        raise HTTPException(404, f"Edge '{req.edge_id}' not found")

    graph.update_edge_feedback(req.edge_id, req.success)

    # Persist updated probabilities
    graph.save(str(DATA_DIR / f"{req.workflow_name}.json"))

    edge = graph.edges[req.edge_id]
    return {
        "edge_id": req.edge_id,
        "updated_probability": round(edge.probability, 3),
        "success_count": edge.success_count,
        "failure_count": edge.failure_count,
        "reliability": round(edge.reliability, 3),
    }


@app.delete("/workflows/{workflow_name}")
def delete_workflow(workflow_name: str):
    """Remove a workflow graph."""
    if workflow_name not in graphs:
        raise HTTPException(404, f"Workflow '{workflow_name}' not found")
    del graphs[workflow_name]
    path = DATA_DIR / f"{workflow_name}.json"
    if path.exists():
        path.unlink()
    return {"deleted": workflow_name}


@app.get("/health")
def health():
    return {"status": "ok", "workflows_loaded": len(graphs)}
