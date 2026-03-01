import json
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

import networkx as nx

from src.canonicalizer import CanonicalEvent


@dataclass
class IntentNode:
    node_id: str
    url_path: str
    dom_state_hash: str
    semantic_label: str
    action_context: str
    visit_count: int = 0
    confidence: float = 0.5
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class IntentEdge:
    edge_id: str
    from_node: str
    to_node: str
    event_type: str
    target_role: str
    text_label: str
    selector_hash: str
    network_signature: str
    probability: float = 1.0
    success_count: int = 0
    failure_count: int = 0
    value: Optional[str] = None

    @property
    def reliability(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return self.probability
        return self.success_count / total

    def to_dict(self):
        d = asdict(self)
        d["reliability"] = self.reliability
        return d


class IntentGraph:

    def __init__(self, workflow_name: str):
        self.workflow_name = workflow_name
        self.graph = nx.DiGraph()
        self.nodes: dict[str, IntentNode] = {}
        self.edges: dict[str, IntentEdge] = {}
        self._entry_node: Optional[str] = None

    @property
    def entry_node(self) -> Optional[str]:
        return self._entry_node

    def add_node(self, node: IntentNode) -> None:
        self.nodes[node.node_id] = node
        self.graph.add_node(node.node_id, **node.to_dict())
        if self._entry_node is None:
            self._entry_node = node.node_id

    def add_edge(self, edge: IntentEdge) -> None:
        self.edges[edge.edge_id] = edge
        attrs = {k: v for k, v in edge.to_dict().items()
                 if k not in ("from_node", "to_node")}
        attrs["weight"] = edge.probability
        self.graph.add_edge(edge.from_node, edge.to_node, **attrs)

    def get_path(self, from_node: str, to_node: str) -> list[IntentEdge]:
        try:
            path_nodes = nx.shortest_path(
                self.graph, from_node, to_node,
                weight=lambda u, v, d: 1.0 - d.get("weight", 0.5)
            )
            path_edges = []
            for i in range(len(path_nodes) - 1):
                edge_data = self.graph.get_edge_data(path_nodes[i], path_nodes[i+1])
                if edge_data:
                    edge_id = edge_data.get("edge_id")
                    if edge_id and edge_id in self.edges:
                        path_edges.append(self.edges[edge_id])
            return path_edges
        except nx.NetworkXNoPath:
            return []

    def find_node_by_label(self, label: str) -> Optional[IntentNode]:
        label_lower = label.lower()
        for node in self.nodes.values():
            if node.semantic_label.lower() == label_lower:
                return node
        for node in self.nodes.values():
            if label_lower in node.semantic_label.lower():
                return node
            if label_lower in node.action_context.lower():
                return node
        return None

    def update_edge_feedback(self, edge_id: str, success: bool) -> None:
        if edge_id not in self.edges:
            return
        edge = self.edges[edge_id]
        if success:
            edge.success_count += 1
        else:
            edge.failure_count += 1
        total = edge.success_count + edge.failure_count
        edge.probability = (edge.success_count + 1) / (total + 2)
        if self.graph.has_edge(edge.from_node, edge.to_node):
            self.graph[edge.from_node][edge.to_node]["weight"] = edge.probability

    def get_alternate_paths(self, from_node: str, to_node: str, 
                            exclude_edges: list[str]) -> list[IntentEdge]:
        removed = []
        for eid in exclude_edges:
            if eid in self.edges:
                e = self.edges[eid]
                if self.graph.has_edge(e.from_node, e.to_node):
                    self.graph.remove_edge(e.from_node, e.to_node)
                    removed.append((e.from_node, e.to_node, e))

        path = self.get_path(from_node, to_node)

        for (fn, tn, e) in removed:
            self.graph.add_edge(fn, tn, edge_id=e.edge_id, weight=e.probability, **e.to_dict())

        return path

    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "entry_node": self._entry_node,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": {k: v.to_dict() for k, v in self.edges.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntentGraph":
        g = cls(data["workflow_name"])
        for node_data in data["nodes"].values():
            g.add_node(IntentNode(**{
                k: v for k, v in node_data.items()
            }))
        for edge_data in data["edges"].values():
            clean = {k: v for k, v in edge_data.items() if k != "reliability"}
            g.add_edge(IntentEdge(**clean))
        g._entry_node = data.get("entry_node")
        return g

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str) -> "IntentGraph":
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)

    def summary(self) -> str:
        return (f"IntentGraph '{self.workflow_name}': "
                f"{len(self.nodes)} states, {len(self.edges)} transitions")


class WorkflowMiner:

    def __init__(self, workflow_name: str):
        self.workflow_name = workflow_name
        self.graph = IntentGraph(workflow_name)
        self._sessions_processed = 0

    def mine_session(self, events: list[CanonicalEvent]) -> IntentGraph:
        if len(events) < 2:
            return self.graph

        for event in events:
            node_id = event.state_id
            if node_id not in self.graph.nodes:
                label = self._infer_semantic_label(event)
                node = IntentNode(
                    node_id=node_id,
                    url_path=event.url_path,
                    dom_state_hash=event.dom_state_hash,
                    semantic_label=label,
                    action_context=event.action_context,
                    visit_count=1,
                )
                self.graph.add_node(node)
            else:
                self.graph.nodes[node_id].visit_count += 1

        for i in range(len(events) - 1):
            src_event = events[i]
            dst_event = events[i + 1]

            if src_event.state_id == dst_event.state_id and \
               src_event.event_type not in ("form_submit", "navigation"):
                continue

            edge_id = self._make_edge_id(src_event, dst_event)

            if edge_id not in self.graph.edges:
                edge = IntentEdge(
                    edge_id=edge_id,
                    from_node=src_event.state_id,
                    to_node=dst_event.state_id,
                    event_type=src_event.event_type,
                    target_role=src_event.target_role,
                    text_label=src_event.text_label,
                    selector_hash=src_event.selector_hash,
                    network_signature=src_event.network_signature,
                    probability=1.0,
                    success_count=1,
                    value=src_event.value,
                )
                self.graph.add_edge(edge)
            else:
                self.graph.edges[edge_id].success_count += 1
                total = (self.graph.edges[edge_id].success_count + 
                         self.graph.edges[edge_id].failure_count)
                self.graph.edges[edge_id].probability = min(
                    1.0, 
                    (self.graph.edges[edge_id].success_count + 1) / (total + 2)
                )

        self._sessions_processed += 1
        return self.graph

    def mine_multiple_sessions(
        self, sessions: list[list[CanonicalEvent]]
    ) -> IntentGraph:
        for session in sessions:
            self.mine_session(session)
        return self.graph

    def _infer_semantic_label(self, event: CanonicalEvent) -> str:
        path_parts = [p for p in event.url_path.split("/") if p and p != "N"]
        path_hint = "_".join(path_parts[-2:]) if path_parts else "root"
        ctx = event.action_context
        return f"{ctx}_{path_hint}".strip("_") or "unknown_state"

    def _make_edge_id(self, src: CanonicalEvent, dst: CanonicalEvent) -> str:
        key = f"{src.state_id}:{src.selector_hash}:{src.event_type}:{dst.state_id}"
        return hashlib.md5(key.encode()).hexdigest()[:12]