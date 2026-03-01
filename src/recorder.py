import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

from playwright.async_api import async_playwright, Page, Browser


@dataclass
class RawEvent:
    timestamp: float
    type: str
    url: str
    selector: str = ""
    role: str = ""
    text: str = ""
    placeholder: str = ""
    value: str = ""
    inputType: str = ""
    name: str = ""
    method: str = ""
    endpoint: str = ""
    dom_snapshot: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self):
        return asdict(self)


class BrowserRecorder:

    INJECTION_SCRIPT = """
    (() => {
        if (window.__intentRecorderActive) return;
        window.__intentRecorderActive = true;
        window.__intentEvents = [];

        function getSelector(el) {
            if (!el) return '';
            if (el.id) return '#' + el.id;
            const tag = el.tagName.toLowerCase();
            const cls = Array.from(el.classList).slice(0, 2).join('.');
            const role = el.getAttribute('role') || '';
            return cls ? `${tag}.${cls}` : (role ? `${tag}[role=${role}]` : tag);
        }

        function getRole(el) {
            if (!el) return '';
            return el.getAttribute('role') || el.tagName.toLowerCase() || '';
        }

        function getText(el) {
            if (!el) return '';
            return (el.innerText || el.textContent || el.placeholder || 
                    el.getAttribute('aria-label') || el.value || '').trim().slice(0, 100);
        }

        ['click', 'change', 'submit'].forEach(evt => {
            document.addEventListener(evt, (e) => {
                const el = e.target;
                window.__intentEvents.push({
                    type: evt,
                    timestamp: Date.now() / 1000,
                    selector: getSelector(el),
                    role: getRole(el),
                    text: getText(el),
                    placeholder: el.placeholder || '',
                    value: el.value || '',
                    inputType: el.type || '',
                    name: el.name || '',
                    url: window.location.href,
                });
            }, true);
        });
    })();
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.events: list[RawEvent] = []

    async def record_session(
        self, 
        start_url: str, 
        output_path: Optional[str] = None,
        timeout_seconds: int = 120
    ) -> list[dict]:

        print(f"\nStarting recording session...")
        print(f"   Navigate to: {start_url}")
        print(f"   Timeout: {timeout_seconds}s")
        print(f"   Close the browser when done to save the workflow\n")

        raw_events = []

        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page: Page = await context.new_page()

            await context.add_init_script(self.INJECTION_SCRIPT)

            async def on_request(request):
                if request.resource_type in ("fetch", "xhr"):
                    raw_events.append({
                        "type": "networkrequest",
                        "timestamp": time.time(),
                        "url": page.url,
                        "method": request.method,
                        "endpoint": request.url,
                        "dom_snapshot": "",
                    })

            page.on("request", on_request)

            async def on_navigation(frame):
                if frame == page.main_frame:
                    raw_events.append({
                        "type": "navigation",
                        "timestamp": time.time(),
                        "url": frame.url,
                        "selector": "",
                        "role": "page",
                        "text": "",
                        "dom_snapshot": "",
                    })

            page.on("framenavigated", on_navigation)

            await page.goto(start_url)

            start_time = time.time()
            try:
                while time.time() - start_time < timeout_seconds:
                    await asyncio.sleep(2)

                    try:
                        js_events = await page.evaluate(
                            "() => { const e = window.__intentEvents || []; "
                            "window.__intentEvents = []; return e; }"
                        )
                        for ev in js_events:
                            ev["dom_snapshot"] = await self._get_dom_snapshot(page)
                            raw_events.append(ev)

                        if js_events:
                            print(f"   Captured {len(js_events)} events "
                                  f"(total: {len(raw_events)})")
                    except Exception:
                        pass

            except Exception as e:
                print(f"   Recording ended: {e}")

            await browser.close()

        print(f"\nRecording complete. {len(raw_events)} raw events captured.")

        if output_path:
            Path(output_path).write_text(json.dumps(raw_events, indent=2))
            print(f"   Saved to: {output_path}")

        return raw_events

    async def _get_dom_snapshot(self, page: Page) -> str:
        try:
            return await page.evaluate(
                "() => document.body.innerHTML.slice(0, 2000)"
            )
        except Exception:
            return ""


class ScriptedRecorder:

    @staticmethod
    async def simulate_login_workflow(start_url: str) -> list[dict]:
        t = time.time()
        return [
            {
                "type": "navigation", "timestamp": t,
                "url": start_url, "role": "page",
                "text": "", "selector": "", "dom_snapshot": "<form><input type=text><input type=password><button>Login</button></form>"
            },
            {
                "type": "change", "timestamp": t + 1.2,
                "url": start_url, "selector": "input#username",
                "role": "input", "text": "Username", "placeholder": "Username",
                "value": "admin@company.com", "inputType": "text", "name": "username",
                "dom_snapshot": "<form><input type=text><input type=password><button>Login</button></form>"
            },
            {
                "type": "change", "timestamp": t + 2.5,
                "url": start_url, "selector": "input#password",
                "role": "input", "text": "Password", "placeholder": "Password",
                "value": "secret123", "inputType": "password", "name": "password",
                "dom_snapshot": "<form><input type=text><input type=password><button>Login</button></form>"
            },
            {
                "type": "click", "timestamp": t + 3.1,
                "url": start_url, "selector": "button.login-btn",
                "role": "button", "text": "Login", "placeholder": "",
                "value": "", "inputType": "", "name": "",
                "dom_snapshot": "<form><input type=text><input type=password><button>Login</button></form>"
            },
            {
                "type": "navigation", "timestamp": t + 3.8,
                "url": start_url.replace("/login", "/dashboard"),
                "role": "page", "text": "", "selector": "",
                "dom_snapshot": "<div class=dashboard><h1>Dashboard</h1><nav></nav></div>"
            },
            {
                "type": "click", "timestamp": t + 5.2,
                "url": start_url.replace("/login", "/dashboard"),
                "selector": "nav a.reports",
                "role": "link", "text": "Reports", "placeholder": "",
                "value": "", "inputType": "", "name": "",
                "dom_snapshot": "<div class=dashboard><h1>Dashboard</h1><nav></nav></div>"
            },
            {
                "type": "navigation", "timestamp": t + 5.9,
                "url": start_url.replace("/login", "/reports"),
                "role": "page", "text": "", "selector": "",
                "dom_snapshot": "<div class=reports><h1>Reports</h1><table></table></div>"
            },
        ]

    @staticmethod
    async def simulate_search_workflow(start_url: str) -> list[dict]:
        t = time.time()
        return [
            {
                "type": "navigation", "timestamp": t,
                "url": start_url, "role": "page", "text": "",
                "selector": "", "dom_snapshot": "<div><input type=search><div class=results></div></div>"
            },
            {
                "type": "change", "timestamp": t + 1.0,
                "url": start_url, "selector": "input[type=search]",
                "role": "input", "text": "Search", "placeholder": "Search...",
                "value": "quarterly report", "inputType": "search", "name": "q",
                "dom_snapshot": "<div><input type=search><div class=results></div></div>"
            },
            {
                "type": "click", "timestamp": t + 1.8,
                "url": start_url, "selector": "button.search-submit",
                "role": "button", "text": "Search", "placeholder": "",
                "value": "", "inputType": "", "name": "",
                "dom_snapshot": "<div><input type=search><div class=results></div></div>"
            },
            {
                "type": "navigation", "timestamp": t + 2.3,
                "url": start_url + "?q=quarterly+report",
                "role": "page", "text": "", "selector": "",
                "dom_snapshot": "<div><input type=search><div class=results><div class=result></div></div></div>"
            },
            {
                "type": "click", "timestamp": t + 3.5,
                "url": start_url + "?q=quarterly+report",
                "selector": "div.result:first-child a",
                "role": "link", "text": "Q3 Report 2024", "placeholder": "",
                "value": "", "inputType": "", "name": "",
                "dom_snapshot": "<div><input type=search><div class=results><div class=result></div></div></div>"
            },
        ]