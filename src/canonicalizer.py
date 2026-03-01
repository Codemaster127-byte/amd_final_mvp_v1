import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse


@dataclass
class CanonicalEvent:
    timestamp: float
    event_type: str
    target_role: str
    selector_hash: str
    text_label: str
    url: str
    url_path: str
    dom_state_hash: str
    network_signature: str
    action_context: str
    value: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @property
    def state_id(self) -> str:
        return hashlib.md5(
            f"{self.url_path}:{self.dom_state_hash}".encode()
        ).hexdigest()[:12]


class EventCanonicalizer:

    PASSWORD_FIELDS = {"password", "passwd", "pwd", "secret"}

    def canonicalize(self, raw_event: dict) -> Optional[CanonicalEvent]:
        try:
            event_type = self._classify_event(raw_event)
            if event_type is None:
                return None

            selector = raw_event.get("selector", "")
            text = raw_event.get("text", "") or raw_event.get("placeholder", "") or ""
            url = raw_event.get("url", "")
            dom_snapshot = raw_event.get("dom_snapshot", "")

            return CanonicalEvent(
                timestamp=raw_event.get("timestamp", 0.0),
                event_type=event_type,
                target_role=raw_event.get("role", self._infer_role(selector)),
                selector_hash=self._hash_selector(selector),
                text_label=self._clean_text(text),
                url=url,
                url_path=self._normalize_path(url),
                dom_state_hash=self._hash_dom(dom_snapshot),
                network_signature=self._extract_network_sig(raw_event),
                action_context=self._infer_context(raw_event),
                value=self._safe_value(raw_event),
                metadata=raw_event.get("metadata", {}),
            )
        except Exception as e:
            print(f"[Canonicalizer] Skipping malformed event: {e}")
            return None

    def canonicalize_session(self, raw_events: list[dict]) -> list[CanonicalEvent]:
        events = []
        for raw in raw_events:
            ev = self.canonicalize(raw)
            if ev:
                events.append(ev)
        events.sort(key=lambda e: e.timestamp)
        return events

    def _classify_event(self, raw: dict) -> Optional[str]:
        t = raw.get("type", "").lower()
        mapping = {
            "click": "click",
            "input": "input",
            "change": "input",
            "submit": "form_submit",
            "navigation": "navigation",
            "networkrequest": "network",
            "keydown": None,
            "mouseover": None,
        }
        return mapping.get(t, t if t else None)

    def _infer_role(self, selector: str) -> str:
        sel = selector.lower()
        if any(x in sel for x in ("button", "btn", "[type=submit]")):
            return "button"
        if any(x in sel for x in ("input", "textarea")):
            return "input"
        if "select" in sel:
            return "select"
        if any(x in sel for x in ("a[", "href", "link")):
            return "link"
        if "form" in sel:
            return "form"
        return "element"

    def _hash_selector(self, selector: str) -> str:
        stable = re.sub(r'\d+', 'N', selector)
        return hashlib.md5(stable.encode()).hexdigest()[:8]

    def _clean_text(self, text: str) -> str:
        return re.sub(r'\s+', ' ', text.strip())[:100]

    def _normalize_path(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            path = parsed.path
            return re.sub(r'/\d+', '/N', path)
        except Exception:
            return url

    def _hash_dom(self, dom_snapshot: str) -> str:
        if not dom_snapshot:
            return "empty"
        tags = re.findall(r'<(\w+)', dom_snapshot)
        structure = ",".join(tags[:50])
        return hashlib.md5(structure.encode()).hexdigest()[:8]

    def _extract_network_sig(self, raw: dict) -> str:
        endpoint = raw.get("endpoint", "") or raw.get("url", "")
        method = raw.get("method", "GET")
        path = self._normalize_path(endpoint)
        return f"{method}:{path}"

    def _infer_context(self, raw: dict) -> str:
        text = (raw.get("text", "") + " " + raw.get("selector", "") +
                " " + raw.get("url", "")).lower()

        context_patterns = {
            "login": ["login", "signin", "sign-in", "password", "authenticate"],
            "search": ["search", "query", "find", "filter"],
            "form_submit": ["submit", "save", "confirm", "apply"],
            "navigation": ["nav", "menu", "breadcrumb", "tab"],
            "data_entry": ["input", "form", "field", "enter"],
            "checkout": ["checkout", "payment", "cart", "purchase"],
            "upload": ["upload", "attach", "file"],
        }

        for ctx, patterns in context_patterns.items():
            if any(p in text for p in patterns):
                return ctx
        return "general"

    def _safe_value(self, raw: dict) -> Optional[str]:
        field_type = raw.get("inputType", "").lower()
        field_name = raw.get("name", "").lower()
        if field_type == "password" or field_name in self.PASSWORD_FIELDS:
            return "***MASKED***"
        value = raw.get("value", raw.get("inputValue"))
        return str(value)[:200] if value else None