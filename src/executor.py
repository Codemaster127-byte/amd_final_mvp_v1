"""
Planner & Execution Engine
Converts high-level intent requests into executable action sequences.
Implements multi-strategy execution with self-healing fallback.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from src.graph import IntentGraph, IntentEdge, IntentNode


class ExecutionStrategy(Enum):
    SELECTOR = "selector"          # Direct CSS selector interaction
    TEXT_MATCH = "text_match"      # Find element by visible text
    ROLE_MATCH = "role_match"      # Find element by ARIA role + label
    NETWORK_REPLAY = "network"     # Replay network request directly
    VISION_FALLBACK = "vision"     # Description-based fallback (stub)


@dataclass
class StepResult:
    edge_id: str
    strategy_used: ExecutionStrategy
    success: bool
    confidence: float
    error: Optional[str] = None
    duration_ms: float = 0.0
    state_verified: bool = False


@dataclass
class ExecutionTrace:
    intent: str
    workflow_name: str
    start_time: float
    end_time: float = 0.0
    steps: list[StepResult] = field(default_factory=list)
    overall_success: bool = False
    final_url: str = ""
    error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "workflow_name": self.workflow_name,
            "duration_ms": round(self.duration_ms, 1),
            "overall_success": self.overall_success,
            "final_url": self.final_url,
            "steps": [
                {
                    "edge_id": s.edge_id,
                    "strategy": s.strategy_used.value,
                    "success": s.success,
                    "confidence": round(s.confidence, 3),
                    "duration_ms": round(s.duration_ms, 1),
                    "state_verified": s.state_verified,
                    "error": s.error,
                }
                for s in self.steps
            ],
            "error": self.error,
        }


class IntentExecutor:
    """
    Executes a workflow path by driving a real browser via Playwright.
    
    Strategy priority:
      1. Selector interaction (fastest when selector still matches)
      2. Text-based match (resilient to selector drift)  
      3. Role + label match (ARIA-based, most robust)
      4. Network replay (skip UI entirely for API-backed actions)
    """

    def __init__(self, graph: IntentGraph, headless: bool = True):
        self.graph = graph
        self.headless = headless
        self.execution_history: list[ExecutionTrace] = []

    async def execute_intent(
        self,
        intent_label: str,
        start_url: str,
        dry_run: bool = False,
    ) -> ExecutionTrace:
        """
        Main entry point. Given a high-level intent label, find the target
        state in the graph and execute the path to reach it.
        """
        trace = ExecutionTrace(
            intent=intent_label,
            workflow_name=self.graph.workflow_name,
            start_time=time.time(),
        )

        # 1. Plan: find target node and compute path
        target_node = self.graph.find_node_by_label(intent_label)
        if target_node is None:
            trace.error = f"No state found matching intent: '{intent_label}'"
            trace.end_time = time.time()
            self.execution_history.append(trace)
            return trace

        entry_node = self.graph.entry_node
        if entry_node is None:
            trace.error = "Intent graph has no entry node"
            trace.end_time = time.time()
            self.execution_history.append(trace)
            return trace

        path: list[IntentEdge] = self.graph.get_path(entry_node, target_node.node_id)
        if not path:
            trace.error = f"No path found from entry to '{intent_label}'"
            trace.end_time = time.time()
            self.execution_history.append(trace)
            return trace

        print(f"\n🎯 Executing intent: '{intent_label}'")
        print(f"   Path: {len(path)} steps")
        for i, edge in enumerate(path):
            print(f"   {i+1}. {edge.event_type} → '{edge.text_label}' [{edge.target_role}]")

        if dry_run:
            trace.overall_success = True
            trace.end_time = time.time()
            trace.error = "DRY RUN - no browser interaction"
            self.execution_history.append(trace)
            return trace

        # 2. Execute path in browser
        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page: Page = await context.new_page()

            try:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=15000)

                failed_edges: list[str] = []

                for step_num, edge in enumerate(path):
                    step_result = await self._execute_step(page, edge, failed_edges)
                    trace.steps.append(step_result)

                    # Feed result back into graph (self-healing)
                    self.graph.update_edge_feedback(edge.edge_id, step_result.success)

                    if not step_result.success:
                        # Try alternate path through graph
                        print(f"\n   ⚠️  Step {step_num+1} failed. Seeking alternate path...")
                        failed_edges.append(edge.edge_id)

                        remaining_target = target_node.node_id
                        current_state = edge.from_node
                        alt_path = self.graph.get_alternate_paths(
                            current_state, remaining_target, failed_edges
                        )

                        if alt_path:
                            print(f"   🔄 Self-healing: found alternate path ({len(alt_path)} steps)")
                            path = alt_path  # switch to alternate
                            continue
                        else:
                            trace.error = f"Step {step_num+1} failed, no alternate path available"
                            break

                trace.final_url = page.url
                trace.overall_success = (
                    not trace.error and
                    all(s.success for s in trace.steps)
                )

            except Exception as e:
                trace.error = str(e)
            finally:
                await browser.close()

        trace.end_time = time.time()
        self.execution_history.append(trace)

        status = "✅" if trace.overall_success else "❌"
        print(f"\n{status} Execution {'complete' if trace.overall_success else 'failed'} "
              f"in {trace.duration_ms:.0f}ms")

        return trace

    async def _execute_step(
        self,
        page: Page,
        edge: IntentEdge,
        failed_edges: list[str],
    ) -> StepResult:
        """
        Execute a single edge (transition) using the best available strategy.
        Tries strategies in priority order until one succeeds.
        """
        strategies = self._select_strategies(edge)
        start = time.time()

        for strategy in strategies:
            try:
                success = await self._try_strategy(page, edge, strategy)
                if success:
                    verified = await self._verify_transition(page, edge)
                    duration = (time.time() - start) * 1000
                    print(f"   ✓ [{strategy.value}] '{edge.text_label}' "
                          f"(confidence: {edge.probability:.2f}, {duration:.0f}ms)")
                    return StepResult(
                        edge_id=edge.edge_id,
                        strategy_used=strategy,
                        success=True,
                        confidence=edge.probability,
                        duration_ms=duration,
                        state_verified=verified,
                    )
            except Exception as e:
                continue  # Try next strategy

        duration = (time.time() - start) * 1000
        print(f"   ✗ All strategies failed for '{edge.text_label}'")
        return StepResult(
            edge_id=edge.edge_id,
            strategy_used=strategies[0],
            success=False,
            confidence=0.0,
            error="All strategies exhausted",
            duration_ms=duration,
        )

    def _select_strategies(self, edge: IntentEdge) -> list[ExecutionStrategy]:
        """Order execution strategies based on edge characteristics."""
        strategies = []

        # Network replay first for API-backed actions
        if edge.network_signature and edge.network_signature != "GET:":
            strategies.append(ExecutionStrategy.NETWORK_REPLAY)

        # Text matching is most resilient to UI changes
        if edge.text_label:
            strategies.append(ExecutionStrategy.TEXT_MATCH)

        # Role matching as secondary
        if edge.target_role in ("button", "link", "input"):
            strategies.append(ExecutionStrategy.ROLE_MATCH)

        # Direct selector (fastest but brittle)
        strategies.append(ExecutionStrategy.SELECTOR)

        return strategies

    async def _try_strategy(
        self, page: Page, edge: IntentEdge, strategy: ExecutionStrategy
    ) -> bool:
        """Attempt a single execution strategy."""
        timeout = 5000  # 5 second timeout per strategy

        if strategy == ExecutionStrategy.TEXT_MATCH:
            return await self._execute_by_text(page, edge, timeout)

        elif strategy == ExecutionStrategy.ROLE_MATCH:
            return await self._execute_by_role(page, edge, timeout)

        elif strategy == ExecutionStrategy.SELECTOR:
            return await self._execute_by_selector(page, edge, timeout)

        elif strategy == ExecutionStrategy.NETWORK_REPLAY:
            return await self._execute_network_replay(page, edge, timeout)

        return False

    async def _execute_by_text(self, page: Page, edge: IntentEdge, timeout: int) -> bool:
        if not edge.text_label or edge.text_label == "***MASKED***":
            return False

        if edge.event_type == "click":
            # Try button by text
            try:
                btn = page.get_by_role("button", name=edge.text_label)
                await btn.click(timeout=timeout)
                return True
            except Exception:
                pass
            # Try link by text
            try:
                link = page.get_by_role("link", name=edge.text_label)
                await link.click(timeout=timeout)
                return True
            except Exception:
                pass
            # Try any element with this text
            try:
                elem = page.get_by_text(edge.text_label, exact=False)
                await elem.first.click(timeout=timeout)
                return True
            except Exception:
                return False

        elif edge.event_type in ("input", "change"):
            try:
                field = page.get_by_placeholder(edge.text_label)
                value = edge.value if edge.value and edge.value != "***MASKED***" else ""
                await field.fill(value, timeout=timeout)
                return True
            except Exception:
                return False

        elif edge.event_type == "form_submit":
            try:
                await page.keyboard.press("Enter")
                return True
            except Exception:
                return False

        return False

    async def _execute_by_role(self, page: Page, edge: IntentEdge, timeout: int) -> bool:
        try:
            role_map = {
                "button": "button",
                "link": "link",
                "input": "textbox",
                "select": "combobox",
            }
            aria_role = role_map.get(edge.target_role, edge.target_role)

            if edge.event_type == "click":
                elem = page.get_by_role(aria_role)
                if edge.text_label:
                    elem = page.get_by_role(aria_role, name=edge.text_label)
                await elem.first.click(timeout=timeout)
                return True

            elif edge.event_type in ("input", "change"):
                elem = page.get_by_role("textbox")
                value = edge.value if edge.value and edge.value != "***MASKED***" else ""
                await elem.first.fill(value, timeout=timeout)
                return True

        except Exception:
            return False

        return False

    async def _execute_by_selector(self, page: Page, edge: IntentEdge, timeout: int) -> bool:
        """Try common selector patterns derived from the stored hash."""
        # In production this would use the stored selector - for MVP we try common patterns
        patterns = []

        if edge.target_role == "button":
            patterns = ["button", "[type=submit]", ".btn", ".button"]
        elif edge.target_role == "input":
            patterns = ["input[type=text]", "input[type=email]", "input:not([type=password])"]
        elif edge.target_role == "link":
            patterns = [f"a"]
        else:
            patterns = [edge.target_role]

        for sel in patterns:
            try:
                if edge.event_type == "click":
                    await page.click(sel, timeout=timeout)
                    return True
                elif edge.event_type in ("input", "change"):
                    value = edge.value if edge.value and edge.value != "***MASKED***" else ""
                    await page.fill(sel, value, timeout=timeout)
                    return True
            except Exception:
                continue

        return False

    async def _execute_network_replay(self, page: Page, edge: IntentEdge, timeout: int) -> bool:
        """
        Replay the underlying network request directly (bypass UI).
        Fastest strategy when applicable.
        """
        # For MVP: skip network replay and let other strategies handle it
        # Full implementation would use page.evaluate() to make fetch() calls
        return False

    async def _verify_transition(self, page: Page, edge: IntentEdge) -> bool:
        """Verify we reached the expected state after the action."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
            # In full implementation: check DOM state hash against expected
            return True
        except Exception:
            return False

    def get_execution_stats(self) -> dict:
        """Summary statistics over all execution history."""
        if not self.execution_history:
            return {"total": 0}

        total = len(self.execution_history)
        successful = sum(1 for t in self.execution_history if t.overall_success)
        avg_duration = sum(t.duration_ms for t in self.execution_history) / total

        strategy_counts: dict[str, int] = {}
        for trace in self.execution_history:
            for step in trace.steps:
                s = step.strategy_used.value
                strategy_counts[s] = strategy_counts.get(s, 0) + 1

        return {
            "total_executions": total,
            "successful": successful,
            "success_rate": round(successful / total, 3),
            "avg_duration_ms": round(avg_duration, 1),
            "strategy_usage": strategy_counts,
        }
