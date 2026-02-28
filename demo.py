"""
Intent Protocol Layer — End-to-End Demo Script
Demonstrates: record → mine → plan → execute → self-heal

Run with: python demo.py
"""

import asyncio
import json
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich import print as rprint

from src.canonicalizer import EventCanonicalizer
from src.graph import WorkflowMiner, IntentGraph
from src.executor import IntentExecutor
from src.recorder import ScriptedRecorder

console = Console()


async def demo_mine_and_plan():
    """Full pipeline demo: simulate recording → mine graph → plan execution."""

    console.print(Panel.fit(
        "[bold cyan]Intent Protocol Layer — MVP Demo[/bold cyan]\n"
        "Semantic Bridging Engine for Autonomous Agent Workflows",
        border_style="cyan"
    ))

    # ─────────────────────────────────────────────
    # STEP 1: Simulate a recorded browser session
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 1: Simulating Browser Recording Session[/bold yellow]")
    console.print("  (In production: run BrowserRecorder.record_session() with a real browser)")

    recorder = ScriptedRecorder()
    base_url = "https://app.example.com"
    raw_events = await recorder.simulate_login_workflow(base_url + "/login")

    console.print(f"  ✓ Captured [green]{len(raw_events)}[/green] raw browser events")

    # Show raw events table
    table = Table(title="Raw Browser Events", show_lines=True)
    table.add_column("Time", style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Element", style="green")
    table.add_column("Text/Value")
    table.add_column("URL", style="dim")

    for ev in raw_events:
        table.add_row(
            f"{ev.get('timestamp', 0):.1f}",
            ev.get("type", ""),
            f"{ev.get('role', '')}:{ev.get('selector', '')[:30]}",
            (ev.get("text") or ev.get("value") or "")[:30],
            ev.get("url", "")[-30:],
        )
    console.print(table)

    # ─────────────────────────────────────────────
    # STEP 2: Event Canonicalization
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 2: Event Canonicalization[/bold yellow]")

    canon = EventCanonicalizer()
    canonical_events = canon.canonicalize_session(raw_events)

    console.print(f"  ✓ Normalized to [green]{len(canonical_events)}[/green] canonical events")
    console.print(f"  ✓ Selector hashing: stable across UI changes")
    console.print(f"  ✓ DOM fingerprinting: {len(set(e.dom_state_hash for e in canonical_events))} unique states detected")

    for ev in canonical_events[:3]:
        console.print(
            f"    [{ev.event_type}] role={ev.target_role} "
            f"text='{ev.text_label}' "
            f"ctx={ev.action_context} "
            f"state={ev.state_id}"
        )

    # ─────────────────────────────────────────────
    # STEP 3: Workflow Mining → Intent Graph
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 3: Workflow Mining → Intent Graph[/bold yellow]")

    miner = WorkflowMiner("enterprise_login")
    graph = miner.mine_session(canonical_events)

    # Mine a second session for reinforcement
    raw_events2 = await recorder.simulate_login_workflow(base_url + "/login")
    canonical2 = canon.canonicalize_session(raw_events2)
    miner.mine_session(canonical2)

    console.print(f"  ✓ {graph.summary()}")
    console.print(f"  ✓ Processed 2 sessions for transition probability reinforcement")

    # Show the intent graph as a tree
    tree = Tree("🔵 Intent Graph", guide_style="cyan")
    for node in graph.nodes.values():
        node_label = (
            f"[cyan]{node.semantic_label}[/cyan] "
            f"[dim](state: {node.node_id}, visits: {node.visit_count})[/dim]"
        )
        branch = tree.add(node_label)

        # Add outgoing edges
        for edge in graph.edges.values():
            if edge.from_node == node.node_id:
                to_node = graph.nodes.get(edge.to_node)
                to_label = to_node.semantic_label if to_node else edge.to_node[:8]
                branch.add(
                    f"──[green]{edge.event_type}[/green]→ '{edge.text_label}' "
                    f"→ {to_label} "
                    f"[dim](p={edge.probability:.2f})[/dim]"
                )

    console.print(tree)

    # ─────────────────────────────────────────────
    # STEP 4: Intent Planning (dry run)
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 4: Intent Planning (Dry Run)[/bold yellow]")

    executor = IntentExecutor(graph, headless=True)

    # Find a reachable intent
    target_labels = [n.semantic_label for n in graph.nodes.values()]
    console.print(f"  Available intent targets: {target_labels}")

    # Pick the last node as target (e.g., "reports" page)
    target = target_labels[-1] if target_labels else "dashboard"
    console.print(f"  → Planning path to: '[cyan]{target}[/cyan]'")

    trace = await executor.execute_intent(
        intent_label=target,
        start_url=base_url + "/login",
        dry_run=True,
    )

    console.print(f"\n  [green]Plan generated:[/green]")
    entry = graph.entry_node
    path = graph.get_path(entry, graph.find_node_by_label(target).node_id
                          if graph.find_node_by_label(target) else entry)

    plan_table = Table(title=f"Execution Plan: '{target}'", show_lines=True)
    plan_table.add_column("#", style="dim")
    plan_table.add_column("Action", style="cyan")
    plan_table.add_column("Target", style="green")
    plan_table.add_column("Strategy Priority")
    plan_table.add_column("Confidence", justify="right")

    for i, edge in enumerate(path):
        strategies = []
        if edge.text_label:
            strategies.append("text_match")
        if edge.target_role:
            strategies.append("role_match")
        strategies.append("selector")

        plan_table.add_row(
            str(i + 1),
            edge.event_type,
            f"'{edge.text_label}' [{edge.target_role}]",
            " → ".join(strategies),
            f"{edge.probability:.0%}",
        )

    console.print(plan_table)

    # ─────────────────────────────────────────────
    # STEP 5: Self-Healing Simulation
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 5: Self-Healing Demonstration[/bold yellow]")
    console.print("  Simulating UI change: a selector breaks (transition probability drops)...")

    if path:
        first_edge = path[0]
        # Simulate 3 failures
        for _ in range(3):
            graph.update_edge_feedback(first_edge.edge_id, success=False)

        updated_edge = graph.edges[first_edge.edge_id]
        console.print(
            f"  ✓ Edge '{first_edge.text_label}' probability: "
            f"[yellow]{first_edge.probability:.2f}[/yellow] → "
            f"[red]{updated_edge.probability:.2f}[/red] (flagged as unreliable)"
        )
        console.print("  ✓ Planner will now prefer alternate paths or strategies")
        console.print("  ✓ System continues operating — no manual intervention needed")

    # ─────────────────────────────────────────────
    # STEP 6: Save Graph
    # ─────────────────────────────────────────────
    console.print("\n[bold yellow]Step 6: Persist Intent Graph[/bold yellow]")
    graph_path = "data/enterprise_login.json"
    import os
    os.makedirs("data", exist_ok=True)
    graph.save(graph_path)
    console.print(f"  ✓ Graph saved to [cyan]{graph_path}[/cyan]")

    # ─────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────
    console.print(Panel(
        f"[bold green]Demo Complete ✓[/bold green]\n\n"
        f"  Workflow:   [cyan]{graph.workflow_name}[/cyan]\n"
        f"  States:     [cyan]{len(graph.nodes)}[/cyan]\n"
        f"  Transitions:[cyan]{len(graph.edges)}[/cyan]\n"
        f"  Sessions:   [cyan]2[/cyan]\n\n"
        f"[dim]Next steps:\n"
        f"  1. Start API:  uvicorn api.main:app --reload\n"
        f"  2. POST /workflows/simulate  (try more scenarios)\n"
        f"  3. POST /execute  (run against a real URL)\n"
        f"  4. POST /feedback (update transition probabilities)[/dim]",
        border_style="green",
        title="Intent Protocol Layer — MVP"
    ))

    return graph


async def demo_search_workflow():
    """Secondary demo: search workflow mining."""
    console.print("\n[bold cyan]--- Search Workflow Demo ---[/bold cyan]")

    recorder = ScriptedRecorder()
    canon = EventCanonicalizer()

    raw = await recorder.simulate_search_workflow("https://app.example.com/search")
    events = canon.canonicalize_session(raw)

    miner = WorkflowMiner("document_search")
    graph = miner.mine_session(events)

    console.print(f"  ✓ {graph.summary()}")
    for edge in graph.edges.values():
        from_node = graph.nodes.get(edge.from_node)
        to_node = graph.nodes.get(edge.to_node)
        print(f"    {from_node.semantic_label if from_node else '?'} "
              f"--[{edge.event_type}: {edge.text_label}]--> "
              f"{to_node.semantic_label if to_node else '?'}")

    return graph


if __name__ == "__main__":
    console.print()
    asyncio.run(demo_mine_and_plan())
    console.print()
    asyncio.run(demo_search_workflow())
