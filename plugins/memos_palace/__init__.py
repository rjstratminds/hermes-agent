"""memos_palace — composite MemoryProvider for memOS + MemPalace.

memOS:
- semantic recall over HTTP JSON
- promotability-gated, async turn storage

MemPalace:
- verbatim archive recall over MCP JSON-RPC over HTTPS
- verbatim archive storage over MCP JSON-RPC over HTTPS
- intended to run behind a proxy that injects the real bearer

This provider is deliberately non-blocking where possible:
- pre-turn recall prefers MemOS and only falls back to MemPalace when precision
  or weak-coverage conditions are met
- turn writes are queued to a background worker for MemOS promotion and
  MemPalace archival
- backend failures degrade to empty context and logged warnings
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from agent.memory_provider import MemoryProvider
from hermes_cli.onecli_bootstrap import DEFAULT_CONFIG_URLS, extract_bootstrap_env, fetch_first_container_config
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_MEMOS_SEARCH_TIMEOUT = 5.0
_MEMOS_STORE_TIMEOUT = 15.0
_MEMPALACE_TIMEOUT = 8.0
_MIN_CONTENT_LENGTH = 50
_RECENT_DEDUP_WINDOW_SECS = 300
_RECENT_DEDUP_MAX = 64
_PREFETCH_MAX_WAIT_SECS = 0.75

_MEMORY_BLOCK_RE = re.compile(r"<(?:memory|mempalace|memory-context)>[\s\S]*?</(?:memory|mempalace|memory-context)>", re.I)
_SYSTEM_NOTE_RE = re.compile(r"\[System note:[^\]]+\]\s*", re.I)
_TRANSPORT_META_RE = re.compile(
    r"^\s*(message_id|sender_id|conversation_label|is_group_chat|platform|chat_id|user_id)\s*:\s*.*$",
    re.I | re.M,
)
_EXPLICIT_MEMORY_RE = re.compile(r"\b(remember|don't forget|dont forget|mem it|save this|store this)\b", re.I)
_IDENTITY_DECISION_RE = re.compile(r"\b(i am|my name is|prefer|always|never|decision|decided|policy|priority|important)\b", re.I)
_PROJECT_RE = re.compile(r"\b(project|implemented|integration|config|configured|setup|workflow|notion|memory)\b", re.I)
_NOTION_LINK_RE = re.compile(r"https?://(?:www\.)?notion\.so/\S+", re.I)
_LITERAL_ENV_REF_RE = re.compile(r"^\$(?:\{)?[A-Za-z_][A-Za-z0-9_]*(?:\})?$")
_ONECLI_STABLE_COMBINED_CA = "/tmp/onecli-combined-ca.pem"
_VERBATIM_INTENT_RE = re.compile(
    r"\b(exact words?|word for word|verbatim|quote|quoted|transcript|what did (?:he|she|they|you) say)\b",
    re.I,
)
_PRECISION_INTENT_RE = re.compile(
    r"\b(precise|precision|exact phrasing|exact wording|specific wording)\b",
    re.I,
)
_RECALL_INTENT_RE = re.compile(r"\b(remember|recall|earlier|previously said|what was said)\b", re.I)
_EVIDENCE_INTENT_RE = re.compile(r"\b(evidence|reported|medical|scientific|citation|source)\b", re.I)


def _load_config() -> dict:
    """Load provider config from env plus optional JSON file in HERMES_HOME."""
    from hermes_constants import get_hermes_home

    hostname = socket.gethostname()
    cfg = {
        "memos_api_url": os.environ.get("MEMOS_API_URL", "").strip(),
        "memos_api_key": os.environ.get("MEMOS_API_KEY", "").strip(),
        "mempalace_mcp_url": os.environ.get("MEMPALACE_MCP_URL", "").strip(),
        "owner_user_id": os.environ.get("MEMOS_OWNER_USER_ID", "").strip() or os.environ.get("MEMPALACE_DEFAULT_OWNER", "").strip(),
        "memos_namespace": os.environ.get("MEMOS_NAMESPACE", "").strip() or f"platform:hermes/node:{hostname}",
        "memos_source": os.environ.get("MEMOS_SOURCE", "hermes").strip(),
        "memos_server": os.environ.get("MEMOS_SERVER", "openclaw-gcp").strip(),
        "memos_node": os.environ.get("MEMOS_NODE", hostname).strip(),
        "mempalace_wing": os.environ.get("MEMPALACE_WING", "agent_main").strip(),
        "mempalace_room": os.environ.get("MEMPALACE_ROOM", "conversations").strip(),
        "principals_path": os.environ.get("MEMPALACE_PRINCIPALS_PATH", "").strip(),
    }

    cfg_path = get_hermes_home() / "memos_palace.json"
    if cfg_path.exists():
        try:
            file_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in file_cfg.items() if v not in (None, "")})
        except Exception as exc:
            logger.debug("memos_palace: failed to read config file: %s", exc)

    return cfg


def _sanitize_for_storage(text: str) -> str:
    text = _MEMORY_BLOCK_RE.sub("", text or "")
    text = _SYSTEM_NOTE_RE.sub("", text)
    text = _TRANSPORT_META_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trim_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True)


class MemosPalaceProvider(MemoryProvider):
    def __init__(self) -> None:
        self._config: dict = {}
        self._session_id = ""
        self._platform = "cli"
        self._agent_context = "primary"
        self._hermes_home = ""
        self._gateway_user_id = ""

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="memos-palace",
        )
        self._prefetch_future: Optional[concurrent.futures.Future[str]] = None
        self._prefetch_lock = threading.Lock()
        self._prefetch_result = ""

        self._write_queue: queue.Queue[Optional[dict]] = queue.Queue()
        self._write_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._recent_hashes: dict[str, float] = {}
        self._recent_lock = threading.Lock()
        self._mcp_initialized = False
        self._mcp_lock = threading.Lock()
        self._mempalace_proxy_url = ""
        self._mempalace_verify: str | bool = True

    @property
    def name(self) -> str:
        return "memos_palace"

    def is_available(self) -> bool:
        cfg = _load_config()
        memos_ok = bool(cfg.get("memos_api_url"))
        mempalace_ok = bool(cfg.get("mempalace_mcp_url"))
        return memos_ok or mempalace_ok

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._session_id = session_id
        self._platform = kwargs.get("platform", "cli")
        self._agent_context = kwargs.get("agent_context", "primary")
        self._hermes_home = kwargs.get("hermes_home", "")
        self._gateway_user_id = kwargs.get("user_id", "") or ""
        self._configure_mempalace_transport()

        if self._write_thread is None:
            self._write_thread = threading.Thread(
                target=self._write_worker,
                name="memos-palace-writer",
                daemon=True,
            )
            self._write_thread.start()

    def system_prompt_block(self) -> str:
        enabled = []
        if self._memos_enabled:
            enabled.append("memOS primary semantic memory")
        if self._mempalace_enabled:
            enabled.append("MemPalace verbatim archive and fallback recall")
        if not enabled:
            return ""
        return (
            "External memory provider active: "
            + ", ".join(enabled)
            + ". Treat MemOS as the semantic authority and MemPalace as the verbatim archive for evidence and exact recall."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            future = self._prefetch_future

        if future is not None and future.done():
            try:
                result = future.result()
                if result is not None:
                    self._prefetch_result = result
            except Exception as exc:
                logger.debug("memos_palace: queued prefetch failed: %s", exc)
            finally:
                with self._prefetch_lock:
                    if self._prefetch_future is future:
                        self._prefetch_future = None

        if self._prefetch_result:
            return self._prefetch_result

        if not query.strip():
            return ""

        # Best-effort cold-start recall. Keep it bounded so first turn isn't stalled.
        try:
            future = self._executor.submit(self._build_recall_context, query, session_id or self._session_id)
            return future.result(timeout=_PREFETCH_MAX_WAIT_SECS) or ""
        except Exception:
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not query.strip():
            return
        with self._prefetch_lock:
            if self._prefetch_future is not None and not self._prefetch_future.done():
                return
            self._prefetch_future = self._executor.submit(
                self._build_recall_context, query, session_id or self._session_id
            )

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._agent_context not in ("primary", ""):
            return
        user_text = _sanitize_for_storage(user_content)
        assistant_text = _sanitize_for_storage(assistant_content)
        if len(user_text) + len(assistant_text) < _MIN_CONTENT_LENGTH:
            return
        if self._is_recent_duplicate(user_text, assistant_text):
            return
        payload = {
            "user": user_text,
            "assistant": assistant_text,
            "session_id": session_id or self._session_id,
            "ts": time.time(),
        }
        self._write_queue.put(payload)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "memos_search",
                "description": "Search semantic memory from memOS for relevant facts and past conclusions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for."},
                        "top_k": {"type": "integer", "description": "Maximum results (default 5)."},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memos_store",
                "description": "Explicitly store a durable fact into memOS.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Fact or conclusion to store."},
                        "memory_type": {"type": "string", "description": "Optional memory type tag."},
                        "tier": {"type": "string", "description": "Optional tier, e.g. A or B."},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memos_delete",
                "description": "Delete a memOS memory by memory_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "The memory identifier to delete."},
                    },
                    "required": ["memory_id"],
                },
            },
            {
                "name": "mempalace_search",
                "description": "Search MemPalace verbatim archive for exact past conversation evidence.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for."},
                        "limit": {"type": "integer", "description": "Maximum results (default 3)."},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            if tool_name == "memos_search":
                res = self._memos_search(args.get("query", ""), top_k=int(args.get("top_k", 5)))
                return _json_dumps({"success": True, "results": res})
            if tool_name == "memos_store":
                res = self._memos_store_explicit(
                    args.get("content", ""),
                    memory_type=str(args.get("memory_type", "") or "note"),
                    tier=str(args.get("tier", "") or ""),
                )
                return _json_dumps(res)
            if tool_name == "memos_delete":
                res = self._memos_delete(str(args.get("memory_id", "") or ""))
                return _json_dumps(res)
            if tool_name == "mempalace_search":
                res = self._mempalace_search(args.get("query", ""), limit=int(args.get("limit", 3)))
                return _json_dumps({"success": True, "results": res})
        except Exception as exc:
            return tool_error(f"{tool_name} failed: {exc}")
        return tool_error(f"Unknown memos_palace tool: {tool_name}")

    def shutdown(self) -> None:
        self._stop_event.set()
        self._write_queue.put(None)
        if self._write_thread and self._write_thread.is_alive():
            self._write_thread.join(timeout=1.5)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "memos_api_url",
                "description": "memOS base URL (e.g. http://openclaw-gcp.tailnet:8000)",
                "required": False,
                "env_var": "MEMOS_API_URL",
            },
            {
                "key": "mempalace_mcp_url",
                "description": "MemPalace MCP HTTPS endpoint",
                "required": False,
                "env_var": "MEMPALACE_MCP_URL",
            },
            {
                "key": "owner_user_id",
                "description": "Canonical owner identity used for private recall/store",
                "required": False,
                "env_var": "MEMOS_OWNER_USER_ID",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / "memos_palace.json"
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update({k: v for k, v in values.items() if v not in (None, "")})
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if action in {"add", "replace"} and target in {"memory", "user"} and content.strip():
            self._write_queue.put({
                "user": "",
                "assistant": _sanitize_for_storage(content),
                "session_id": self._session_id,
                "explicit": True,
                "ts": time.time(),
            })

    @property
    def _memos_enabled(self) -> bool:
        return bool(self._config.get("memos_api_url"))

    @property
    def _mempalace_enabled(self) -> bool:
        return bool(self._config.get("mempalace_mcp_url"))

    def _configure_mempalace_transport(self) -> None:
        proxy = self._first_valid_env("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
        verify = self._first_valid_env(
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "ONECLI_BOOTSTRAP_SSL_CERT_FILE",
            "NODE_EXTRA_CA_CERTS",
            "ONECLI_BOOTSTRAP_NODE_EXTRA_CA_CERTS",
        )

        if not proxy:
            try:
                config, _ = fetch_first_container_config(DEFAULT_CONFIG_URLS, timeout=1.5)
                bootstrap_env = extract_bootstrap_env(config)
            except Exception as exc:
                logger.debug("memos_palace: onecli bootstrap unavailable: %s", exc)
                bootstrap_env = {}
            proxy = bootstrap_env.get("ONECLI_BOOTSTRAP_PROXY_URL", "")
            verify = verify or bootstrap_env.get("ONECLI_BOOTSTRAP_SSL_CERT_FILE", "")
            verify = verify or bootstrap_env.get("ONECLI_BOOTSTRAP_NODE_EXTRA_CA_CERTS", "")

        self._mempalace_proxy_url = proxy or ""
        self._mempalace_verify = self._resolve_mempalace_verify_path(verify)

    def _resolve_mempalace_verify_path(self, verify: str) -> str | bool:
        stable_ca = Path(_ONECLI_STABLE_COMBINED_CA)
        if stable_ca.is_file():
            return str(stable_ca)
        if verify:
            return verify
        return True

    def _first_valid_env(self, *names: str) -> str:
        for name in names:
            value = str(os.environ.get(name, "") or "").strip()
            if not value or _LITERAL_ENV_REF_RE.fullmatch(value):
                continue
            return value
        return ""

    def _write_worker(self) -> None:
        while not self._stop_event.is_set():
            item = self._write_queue.get()
            if item is None:
                self._write_queue.task_done()
                break
            try:
                self._store_turn(item)
            except Exception as exc:
                logger.warning("memos_palace: async store failed: %s", exc)
            finally:
                self._write_queue.task_done()

    def _build_recall_context(self, query: str, session_id: str) -> str:
        blocks: List[str] = []
        memos_results: List[dict] = []
        if self._memos_enabled:
            try:
                memos_results = self._memos_search(query, 5)
            except Exception as exc:
                logger.debug("memos_palace: memOS recall failed: %s", exc)
                memos_results = []

        if memos_results:
            body = "\n".join(f"{i + 1}. {item['text']}" for i, item in enumerate(memos_results[:5]))
            blocks.append(f"<memory>\nMemOS recall:\n{body}\n</memory>")

        if self._mempalace_enabled and self._should_use_mempalace_recall(query, memos_results):
            try:
                mempalace_results = self._mempalace_search(query, 3)
            except Exception as exc:
                logger.debug("memos_palace: mempalace recall failed: %s", exc)
                mempalace_results = []
            if mempalace_results:
                body = "\n".join(
                    f"{i + 1}. {_trim_text(item.get('text', ''), 500)} [{item.get('wing', 'agent_main')}/{item.get('room', 'conversations')}]"
                    for i, item in enumerate(mempalace_results[:3])
                )
                blocks.append(
                    "<mempalace>\n"
                    "MemPalace fallback recall:\n"
                    f"{body}\n"
                    "Use these hits as contextual or verbatim evidence, not as canonical semantic replacements.\n"
                    "</mempalace>"
                )
        return "\n\n".join(blocks)

    def _store_turn(self, item: dict) -> None:
        user_text = item.get("user", "")
        assistant_text = item.get("assistant", "")
        explicit = bool(item.get("explicit"))

        if self._memos_enabled and (explicit or self._should_store_memos(user_text, assistant_text)):
            self._store_memos_turn(user_text, assistant_text)
        if self._mempalace_enabled:
            self._store_mempalace_turn(
                user_text,
                assistant_text,
                session_id=str(item.get("session_id", "") or self._session_id),
            )

    def _should_store_memos(self, user_text: str, assistant_text: str) -> bool:
        combined = f"{user_text}\n\n{assistant_text}".strip()
        if len(combined) < _MIN_CONTENT_LENGTH:
            return False

        score = 0
        if _EXPLICIT_MEMORY_RE.search(combined):
            score += 4
        if _IDENTITY_DECISION_RE.search(combined):
            score += 2
        if _PROJECT_RE.search(combined):
            score += 1
        if len(assistant_text.split()) > 150:
            score += 2
        if _NOTION_LINK_RE.search(assistant_text):
            score = max(score, 5)
        if len(user_text.split()) < 6 and len(assistant_text.split()) < 20:
            score -= 2
        return score >= 2

    def _should_use_mempalace_recall(self, query: str, memos_results: List[dict]) -> bool:
        if not query.strip():
            return False
        if (
            _VERBATIM_INTENT_RE.search(query)
            or _PRECISION_INTENT_RE.search(query)
            or _RECALL_INTENT_RE.search(query)
            or _EVIDENCE_INTENT_RE.search(query)
        ):
            return True
        return len(memos_results) < 2

    def _is_recent_duplicate(self, user_text: str, assistant_text: str) -> bool:
        digest = hashlib.sha256(f"{user_text[:200]}\n{assistant_text[:200]}".encode("utf-8")).hexdigest()
        now = time.time()
        with self._recent_lock:
            stale = [k for k, ts in self._recent_hashes.items() if now - ts > _RECENT_DEDUP_WINDOW_SECS]
            for key in stale:
                self._recent_hashes.pop(key, None)
            if digest in self._recent_hashes:
                return True
            self._recent_hashes[digest] = now
            if len(self._recent_hashes) > _RECENT_DEDUP_MAX:
                oldest = sorted(self._recent_hashes.items(), key=lambda kv: kv[1])[: len(self._recent_hashes) - _RECENT_DEDUP_MAX]
                for key, _ in oldest:
                    self._recent_hashes.pop(key, None)
        return False

    def _owner_user_id(self) -> str:
        owner = str(self._config.get("owner_user_id", "") or "").strip()
        if owner:
            return owner
        if self._gateway_user_id:
            return self._gateway_user_id
        return "hermes-user"

    def _memos_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        api_key = str(self._config.get("memos_api_key", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _memos_search(self, query: str, top_k: int = 5) -> List[dict]:
        if not self._memos_enabled or not query.strip():
            return []
        payload = {
            "query": query,
            "user_id": self._owner_user_id(),
            "readable_cube_ids": [self._config["memos_namespace"], self._owner_user_id()],
            "top_k": top_k,
            "include_preference": True,
            "pref_top_k": 3,
            "search_tool_memory": True,
            "tool_mem_top_k": 2,
            "include_skill_memory": True,
            "skill_mem_top_k": 2,
            "mode": "fast",
            "dedup": "mmr",
            "relativity": 0.15,
            "session_id": self._session_id or None,
        }
        url = self._config["memos_api_url"].rstrip("/") + "/product/search"
        with httpx.Client(timeout=_MEMOS_SEARCH_TIMEOUT) as client:
            response = client.post(url, headers=self._memos_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
        return self._rerank_memories(self._extract_memos_hits(data), top_k=top_k)

    def _memos_store_explicit(self, content: str, *, memory_type: str, tier: str) -> dict:
        content = _sanitize_for_storage(content)
        if not content:
            return {"success": False, "error": "empty content"}
        result = self._post_memos_messages(
            [{"role": "assistant", "content": _trim_text(content, 2000)}],
            memory_type=memory_type or "note",
            tier=tier or "",
        )
        return {"success": True, "result": result}

    def _memos_delete(self, memory_id: str) -> dict:
        if not self._memos_enabled:
            return {"success": False, "error": "memOS not configured"}
        if not memory_id:
            return {"success": False, "error": "memory_id is required"}
        url = self._config["memos_api_url"].rstrip("/") + "/product/delete_memory"
        payload = {"user_id": self._owner_user_id(), "memory_id": memory_id}
        with httpx.Client(timeout=_MEMOS_STORE_TIMEOUT) as client:
            response = client.post(url, headers=self._memos_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
        return {"success": True, "result": data}

    def _store_memos_turn(self, user_text: str, assistant_text: str) -> None:
        memory_type, tier = self._infer_memory_shape(user_text, assistant_text)
        messages = []
        if user_text:
            messages.append({"role": "user", "content": _trim_text(user_text, 2000)})
        if assistant_text:
            messages.append({"role": "assistant", "content": _trim_text(assistant_text, 2000)})
        if messages:
            self._post_memos_messages(messages, memory_type=memory_type, tier=tier)

    def _post_memos_messages(self, messages: List[dict], *, memory_type: str, tier: str) -> dict:
        if not self._memos_enabled:
            return {}
        tags = [self._config["memos_source"], f"memory_type:{memory_type}"]
        if tier:
            tags.append(f"tier:{tier}")
        payload = {
            "user_id": self._owner_user_id(),
            "writable_cube_ids": [self._config["memos_namespace"]],
            "messages": messages,
            "custom_tags": tags,
            "info": {
                "source_type": "hermes_chat",
                "memory_type": memory_type,
                "agent_id": f"hermes:{self._config['memos_node']}",
                "conversation_id": self._session_id,
                "tier": tier or None,
            },
            "metadata": {
                "source": self._config["memos_source"],
                "server": self._config["memos_server"],
                "node": self._config["memos_node"],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "async_mode": "async",
            "session_id": self._session_id or None,
        }
        url = self._config["memos_api_url"].rstrip("/") + "/product/add"
        with httpx.Client(timeout=_MEMOS_STORE_TIMEOUT) as client:
            response = client.post(url, headers=self._memos_headers(), json=payload)
            response.raise_for_status()
            return response.json()

    def _store_mempalace_turn(self, user_text: str, assistant_text: str, *, session_id: str) -> None:
        clean_user = _sanitize_for_storage(user_text)
        clean_assistant = _sanitize_for_storage(assistant_text)
        if len(clean_user) + len(clean_assistant) < _MIN_CONTENT_LENGTH:
            return

        scope = self._resolve_scope()
        conversation_id = session_id or self._session_id or "unknown"
        tags = [
            str(self._config.get("memos_source", "hermes") or "hermes"),
            "verbatim_archive",
            f"scope:{scope['scope_type']}",
            f"viewer:{scope['viewer_user_id']}",
            f"subject:{scope['subject_user_id']}",
            f"conversation:{conversation_id}",
            f"platform:{self._platform or 'cli'}",
            f"context:{self._agent_context or 'primary'}",
        ]
        tags.extend(f"participant:{principal}" for principal in scope.get("participants", []))
        deduped_tags = list(dict.fromkeys(tag for tag in tags if tag))

        content = "\n\n".join(
            part
            for part in (
                f"User: {_trim_text(clean_user, 3000)}" if clean_user else "",
                f"Assistant: {_trim_text(clean_assistant, 3000)}" if clean_assistant else "",
            )
            if part
        )
        if not content:
            return

        self._ensure_mempalace_initialized()
        self._mcp_call(
            "tools/call",
            {
                "name": "add_drawer",
                "arguments": {
                    "content": content,
                    "wing": self._config.get("mempalace_wing", "agent_main") or "agent_main",
                    "room": self._config.get("mempalace_room", "conversations") or "conversations",
                    "tags": deduped_tags,
                },
            },
        )

    def _extract_memos_hits(self, data: Any) -> List[dict]:
        hits: List[dict] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                # Current memOS product API puts the rendered memory under
                # `memory`; older schemas used `text` or `content`. Accept all
                # three so a schema drift on the server doesn't silently drop
                # every hit.
                text = node.get("text") or node.get("content") or node.get("memory")
                if isinstance(text, str) and text.strip():
                    metadata = node.get("metadata") or {}
                    # memOS nests tags/info under metadata; surface them so
                    # _rerank_memories can read tier/memory_type.
                    info = node.get("info") or (metadata.get("info") if isinstance(metadata, dict) else None) or {}
                    tags = (
                        node.get("tags")
                        or node.get("custom_tags")
                        or (metadata.get("tags") if isinstance(metadata, dict) else None)
                        or []
                    )
                    hits.append({
                        "id": str(node.get("id", "") or ""),
                        "text": _trim_text(text.strip(), 500),
                        "metadata": metadata,
                        "tags": tags,
                        "info": info,
                    })
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(data)

        deduped = []
        seen = set()
        for item in hits:
            key = item["text"]
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _rerank_memories(self, hits: List[dict], *, top_k: int) -> List[dict]:
        ranked = []
        now = time.time()
        for item in hits:
            score = 1.0
            tags = {str(t) for t in item.get("tags", [])}
            info = item.get("info", {}) or {}
            metadata = item.get("metadata", {}) or {}

            tier = str(info.get("tier", "") or "")
            if not tier:
                if "tier:A" in tags:
                    tier = "A"
                elif "tier:B" in tags:
                    tier = "B"
            if tier == "A":
                score *= 1.35
            elif tier == "B":
                score *= 1.15

            memory_type = str(info.get("memory_type", "") or "")
            if not memory_type:
                for tag in tags:
                    if tag.startswith("memory_type:"):
                        memory_type = tag.split(":", 1)[1]
                        break
            if memory_type in {"event_log", "conversation"}:
                score *= 0.6
            elif memory_type:
                score *= 1.1

            if not tags and not tier:
                score *= 0.85

            ts = metadata.get("ts")
            if isinstance(ts, str):
                try:
                    parsed = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                    age_days = max(0.0, (now - parsed) / 86400.0)
                    if age_days > 30:
                        score *= max(0.7, 1.0 - min(age_days - 30, 180) / 600.0)
                except Exception:
                    pass

            item = dict(item)
            item["score"] = round(score, 4)
            ranked.append(item)

        ranked.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return ranked[:top_k]

    def _infer_memory_shape(self, user_text: str, assistant_text: str) -> tuple[str, str]:
        combined = f"{user_text}\n\n{assistant_text}"
        if _EXPLICIT_MEMORY_RE.search(combined) or _IDENTITY_DECISION_RE.search(combined):
            return "decision", "A"
        if _PROJECT_RE.search(combined) or _NOTION_LINK_RE.search(assistant_text):
            return "project_note", "B"
        return "conversation", ""

    def _resolve_scope(self) -> dict:
        default_owner = self._owner_user_id()
        result = {
            "viewer_user_id": default_owner,
            "subject_user_id": default_owner,
            "scope_type": "private",
            "participants": [default_owner],
        }
        path_text = str(self._config.get("principals_path", "") or "").strip()
        path = Path(path_text) if path_text else (Path(self._hermes_home) / "memos_principals.json" if self._hermes_home else None)
        if not path or not path.exists():
            return result
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("memos_palace: failed to read principals file: %s", exc)
            return result

        senders = config.get("senders", {}) or {}
        groups = config.get("groups", {}) or {}
        group_subjects = config.get("group_subjects", {}) or {}
        platform_key = self._gateway_user_id or default_owner
        viewer = senders.get(platform_key, default_owner)
        result["viewer_user_id"] = viewer
        result["subject_user_id"] = group_subjects.get(self._session_id, viewer)
        group_cfg = groups.get(self._session_id)
        if isinstance(group_cfg, dict):
            result["scope_type"] = group_cfg.get("scope_type", "shared_conversation")
            participants = group_cfg.get("participants") or [viewer]
            result["participants"] = [str(p) for p in participants]
        else:
            result["participants"] = [viewer]
        return result

    def _ensure_mempalace_initialized(self) -> None:
        if not self._mempalace_enabled:
            return
        with self._mcp_lock:
            if self._mcp_initialized:
                return
            self._mcp_call(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "hermes-memos-palace", "version": "0.1.0"},
                },
                request_id=0,
            )
            self._mcp_initialized = True

    def _mempalace_search(self, query: str, limit: int = 3) -> List[dict]:
        if not self._mempalace_enabled or not query.strip():
            return []
        self._ensure_mempalace_initialized()
        scope = self._resolve_scope()
        result = self._mcp_call(
            "tools/call",
            {
                "name": "search_authorized",
                "arguments": {
                    "query": query,
                    "viewer_user_id": scope["viewer_user_id"],
                    "subject_user_id": scope["subject_user_id"],
                    "scope_type": scope["scope_type"],
                    "limit": limit,
                },
            },
        )
        payload = self._extract_mcp_text_payload(result)
        parsed = json.loads(payload) if payload else {}
        results = parsed.get("results", []) if isinstance(parsed, dict) else []
        return [item for item in results if isinstance(item, dict) and item.get("text")]

    def _mcp_call(self, method: str, params: dict, request_id: Optional[int] = None) -> dict:
        url = self._config.get("mempalace_mcp_url", "")
        if not url:
            return {}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer placeholder",
        }
        body = {
            "jsonrpc": "2.0",
            "id": request_id if request_id is not None else int(time.time() * 1000),
            "method": method,
            "params": params,
        }
        client_kwargs = {"timeout": _MEMPALACE_TIMEOUT, "verify": self._mempalace_verify}
        if self._mempalace_proxy_url:
            client_kwargs["proxy"] = self._mempalace_proxy_url
        with httpx.Client(**client_kwargs) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            text = response.text
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            if "error" in payload:
                raise RuntimeError(payload["error"])
            if "result" in payload:
                return payload["result"]
        payload = json.loads(text)
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return payload.get("result", {})

    def _extract_mcp_text_payload(self, result: dict) -> str:
        content = result.get("content", []) if isinstance(result, dict) else []
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict):
            return str(first.get("text", "") or "")
        return ""


def register(ctx) -> None:
    ctx.register_memory_provider(MemosPalaceProvider())
