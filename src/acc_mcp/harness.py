"""Agent Harness & Local LLM Integration for ACC-MCP (Block 2 & R1)."""

from __future__ import annotations

import datetime
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from acc_mcp.server import create_server

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

logger = logging.getLogger(__name__)


@dataclass
class BackendConfig:
    """Configuration for an OpenAI-compatible LLM inference endpoint."""

    name: str = "llama-server"
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen3.5-4b"
    api_key: str = "dummy"
    timeout: float = 30.0

    @classmethod
    def llama_server(
        cls,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "qwen3.5-4b",
    ) -> BackendConfig:
        return cls(name="llama-server", base_url=base_url, model=model)

    @classmethod
    def ollama(
        cls,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "qwen2.5-coder",
    ) -> BackendConfig:
        return cls(name="ollama", base_url=base_url, model=model)

    @classmethod
    def cerebras(
        cls,
        api_key: str | None = None,
        base_url: str = "https://api.cerebras.ai/v1",
        model: str = "qwen-3-27b",
    ) -> BackendConfig:
        key = api_key or os.getenv("CEREBRAS_API_KEY", "dummy")
        return cls(name="cerebras", base_url=base_url, model=model, api_key=key)


@dataclass
class LLMResponse:
    """Standardized response from OpenAI-compatible completion endpoint."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    raw_usage: dict[str, int] = field(default_factory=dict)


@dataclass
class AgentTrace:
    """Audit log capturing MCP handshake, context payload, LLM output, and token metrics."""

    trace_id: str
    timestamp: str
    backend_name: str
    model: str
    file_path: str
    symbol_name: str | None
    task_intent: str
    mcp_tools_discovered: list[str]
    served_tier: int
    context_slice: str
    baseline_tokens: int
    served_tokens: int
    prompt_tokens: int
    completion_tokens: int
    tokens_saved: int
    savings_percentage: float
    response_content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_markdown(self) -> str:
        """Render trace summary in clean GitHub-style Markdown."""
        return (
            f"### Agent Execution Trace (`{self.trace_id}`)\n"
            f"- **Timestamp:** {self.timestamp}\n"
            f"- **Backend:** {self.backend_name} ({self.model})\n"
            f"- **Task Intent:** {self.task_intent}\n"
            f"- **File Target:** `{self.file_path}`"
            + (f" (Symbol: `{self.symbol_name}`)" if self.symbol_name else "")
            + "\n"
            f"- **MCP Tools Discovered:** `{', '.join(self.mcp_tools_discovered)}`\n"
            f"- **Served Context Tier:** Tier {self.served_tier}\n"
            f"- **Token Metrics:**\n"
            f"  - Baseline Uncompressed: **{self.baseline_tokens}** tokens\n"
            f"  - Served Context: **{self.served_tokens}** tokens\n"
            f"  - Actual LLM Prompt: **{self.prompt_tokens}** tokens\n"
            f"  - Actual LLM Completion: **{self.completion_tokens}** tokens\n"
            f"  - **Token Savings:** **{self.tokens_saved}** tokens ({self.savings_percentage:.2f}%)\n\n"
            f"#### Code Context Slice:\n```python\n{self.context_slice}\n```\n\n"
            f"#### Model Generation:\n{self.response_content}\n"
        )


class OpenAIClient:
    """Model-agnostic HTTP client for OpenAI-compatible chat completion endpoints."""

    def __init__(
        self,
        config: BackendConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._client = http_client or httpx.Client(timeout=config.timeout)

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.2) -> LLMResponse:
        """Send chat completion request and parse usage tokens."""
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }

        resp = self._client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=data.get("model", self.config.model),
            raw_usage=usage,
        )


class AgentHarness:
    """Agent harness routing context retrieval via ACC-MCP before prompt dispatch."""

    def __init__(
        self,
        backend_config: BackendConfig | None = None,
        server: MCPServer | None = None,
        client: OpenAIClient | None = None,
    ) -> None:
        self.config = backend_config or BackendConfig.llama_server()
        self.server = server or create_server()
        self.client = client or OpenAIClient(self.config)
        self.traces: list[AgentTrace] = []

    async def execute_task(
        self,
        file_path: str,
        task_intent: str,
        symbol_name: str | None = None,
    ) -> AgentTrace:
        """Execute agent workflow: MCP retrieval -> prompt assembly -> LLM dispatch -> reconciliation."""
        trace_id = f"trace_{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. MCP Handshake & Tool Discovery
        tools = await self.server.list_tools()
        discovered_tool_names = [t.name for t in tools]

        # 2. Context Retrieval through ACC-MCP Server
        mcp_res = await self.server.call_tool(
            "get_adaptive_context",
            {
                "file_path": file_path,
                "task_intent": task_intent,
                "symbol_name": symbol_name,
            },
        )
        context_slice = mcp_res.content[0].text if mcp_res.content else ""

        # Retrieve retrieval metrics from server's budget tracker
        tracker = getattr(self.server, "budget_tracker", None)
        latest_record = tracker.history[-1] if (tracker and tracker.history) else None

        served_tier = latest_record.served_tier if latest_record else 1
        baseline_tokens = latest_record.baseline_tokens if latest_record else 0
        served_tokens = latest_record.served_tokens if latest_record else 0
        tokens_saved = latest_record.tokens_saved if latest_record else 0
        savings_percentage = latest_record.savings_percentage if latest_record else 0.0

        # 3. Assemble Prompt with Compressed Code Context
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert AI programming assistant. "
                    "Use the provided code context to solve the developer task accurately."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"### Context (served by ACC-MCP):\n```python\n{context_slice}\n```\n\n"
                    f"### Developer Task:\n{task_intent}"
                ),
            },
        ]

        # 4. Dispatch to OpenAI-compatible LLM endpoint
        llm_res = self.client.complete(messages)

        # 5. Reconcile Ground-Truth Prompt Tokens with Ledger
        if tracker and latest_record:
            tracker.reconcile_usage(latest_record.query_id, llm_res.prompt_tokens)

        trace = AgentTrace(
            trace_id=trace_id,
            timestamp=now,
            backend_name=self.config.name,
            model=self.config.model,
            file_path=file_path,
            symbol_name=symbol_name,
            task_intent=task_intent,
            mcp_tools_discovered=discovered_tool_names,
            served_tier=served_tier,
            context_slice=context_slice,
            baseline_tokens=baseline_tokens,
            served_tokens=served_tokens,
            prompt_tokens=llm_res.prompt_tokens,
            completion_tokens=llm_res.completion_tokens,
            tokens_saved=tokens_saved,
            savings_percentage=savings_percentage,
            response_content=llm_res.content,
        )

        self.traces.append(trace)
        return trace

    def get_traces(self) -> list[AgentTrace]:
        """Return all execution traces."""
        return self.traces

    def clear_traces(self) -> None:
        """Clear recorded traces."""
        self.traces.clear()
