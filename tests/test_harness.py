"""Integration tests for Agent Harness, Local LLM Integration, and Trace Logger (Block 2 & R1)."""

import json
from pathlib import Path
import httpx
import pytest
from acc_mcp.guard import SecurityViolationError
from acc_mcp.harness import (
    AgentHarness,
    AgentTrace,
    BackendConfig,
    LLMResponse,
    OpenAIClient,
)
from acc_mcp.server import create_server


@pytest.fixture
def sample_code_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "payment_gateway.py"
    code = '''"""Payment gateway integration service."""

class PaymentGateway:
    """Processes online payments with fraud detection."""

    def __init__(self, api_key: str, sandbox: bool = False) -> None:
        """Initialize gateway parameters."""
        self.api_key = api_key
        self.sandbox = sandbox

    def charge(self, customer_id: str, amount_cents: int) -> dict:
        """Charge a customer card."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        return {"status": "success", "charge_id": f"ch_{customer_id}_123"}


def verify_signature(payload: str, signature: str) -> bool:
    """Verify HMAC signature of webhook event."""
    return signature == f"sig_{len(payload)}"
'''
    file_path.write_text(code, encoding="utf-8")
    return file_path


def make_mock_llm_client(
    config: BackendConfig,
    response_text: str = "Use gateway.charge(customer_id, amount_cents) to process payment.",
    prompt_tokens: int = 142,
    completion_tokens: int = 28,
) -> OpenAIClient:
    """Create OpenAIClient backed by an in-memory httpx.MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        data = {
            "id": "chatcmpl-mock-123",
            "object": "chat.completion",
            "model": config.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return httpx.Response(200, json=data)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIClient(config=config, http_client=mock_http)


# ---------------------------------------------------------------------------
# 1. Backend Configuration Presets (Step 8.1)
# ---------------------------------------------------------------------------


def test_backend_config_presets():
    """Verify BackendConfig factory presets for llama-server, Ollama, and Cerebras."""
    llama = BackendConfig.llama_server(base_url="http://127.0.0.1:8080/v1", model="qwen3.5-4b")
    assert llama.name == "llama-server"
    assert llama.base_url == "http://127.0.0.1:8080/v1"
    assert llama.model == "qwen3.5-4b"

    ollama = BackendConfig.ollama(base_url="http://127.0.0.1:11434/v1", model="qwen2.5-coder")
    assert ollama.name == "ollama"
    assert ollama.base_url == "http://127.0.0.1:11434/v1"

    cerebras = BackendConfig.cerebras(api_key="csk-test1234", model="qwen-3-27b")
    assert cerebras.name == "cerebras"
    assert cerebras.api_key == "csk-test1234"
    assert "cerebras.ai" in cerebras.base_url


def test_openai_client_response_parsing():
    """Verify OpenAIClient sends formatted payload and extracts usage tokens."""
    config = BackendConfig.llama_server()
    client = make_mock_llm_client(
        config=config,
        response_text="Generated solution",
        prompt_tokens=210,
        completion_tokens=45,
    )

    resp = client.complete(
        messages=[{"role": "user", "content": "How do I use this?"}],
        temperature=0.1,
    )

    assert isinstance(resp, LLMResponse)
    assert resp.content == "Generated solution"
    assert resp.prompt_tokens == 210
    assert resp.completion_tokens == 45
    assert resp.total_tokens == 255
    assert resp.model == config.model


# ---------------------------------------------------------------------------
# 2. Agent Workflow via ACC-MCP (Step 8.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_harness_end_to_end_inspection(sample_code_file: Path, tmp_path: Path):
    """Verify end-to-end task execution: MCP retrieval -> prompt build -> LLM call -> ledger reconciliation."""
    ledger_path = tmp_path / "harness_ledger.json"
    server = create_server(ledger_path=ledger_path)
    config = BackendConfig.llama_server()
    client = make_mock_llm_client(
        config=config,
        response_text="The charge method accepts customer_id and amount_cents.",
        prompt_tokens=95,
        completion_tokens=15,
    )

    harness = AgentHarness(backend_config=config, server=server, client=client)

    trace = await harness.execute_task(
        file_path=str(sample_code_file),
        task_intent="Inspect PaymentGateway.charge parameters and return type",
        symbol_name="PaymentGateway.charge",
    )

    assert isinstance(trace, AgentTrace)
    assert trace.backend_name == "llama-server"
    assert trace.served_tier == 1
    assert "def charge(self, customer_id: str, amount_cents: int) -> dict:" in trace.context_slice
    assert "..." in trace.context_slice  # Body stubbed

    # Token accounting
    assert trace.baseline_tokens > trace.served_tokens
    assert trace.tokens_saved > 0
    assert trace.savings_percentage > 0.0
    assert trace.prompt_tokens == 95
    assert trace.completion_tokens == 15

    # Verify reconciliation in server ledger
    tracker = getattr(server, "budget_tracker", None)
    assert tracker is not None
    assert tracker.total_requests == 1
    assert tracker.history[0].ground_truth_tokens == 95


@pytest.mark.asyncio
async def test_agent_harness_rewrite_tier3(sample_code_file: Path, tmp_path: Path):
    """Verify rewrite task routes to Tier 3 full implementation body in harness."""
    ledger_path = tmp_path / "harness_ledger.json"
    server = create_server(ledger_path=ledger_path)
    config = BackendConfig.ollama()
    client = make_mock_llm_client(
        config=config,
        response_text="Fixed the validation logic to allow zero amounts for promotional credits.",
        prompt_tokens=220,
        completion_tokens=40,
    )

    harness = AgentHarness(backend_config=config, server=server, client=client)

    trace = await harness.execute_task(
        file_path=str(sample_code_file),
        task_intent="Rewrite PaymentGateway.charge algorithm to support promotional zero-dollar charges",
        symbol_name="PaymentGateway.charge",
    )

    assert trace.served_tier == 3
    # Full implementation body included
    assert "if amount_cents <= 0:" in trace.context_slice
    assert "status" in trace.context_slice
    assert "success" in trace.context_slice
    assert trace.prompt_tokens == 220


# ---------------------------------------------------------------------------
# 3. Trace Logger Verification (Step 8.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_logger_formatting(sample_code_file: Path, tmp_path: Path):
    """Verify trace logger formats comprehensive Markdown summaries and dict exports."""
    server = create_server(ledger_path=tmp_path / "trace_ledger.json")
    config = BackendConfig.llama_server()
    client = make_mock_llm_client(config=config, prompt_tokens=80, completion_tokens=20)
    harness = AgentHarness(backend_config=config, server=server, client=client)

    trace = await harness.execute_task(
        file_path=str(sample_code_file),
        task_intent="Check verify_signature return type",
        symbol_name="verify_signature",
    )

    # Markdown format
    md_report = trace.format_markdown()
    assert f"### Agent Execution Trace (`{trace.trace_id}`)" in md_report
    assert "llama-server" in md_report
    assert "Token Savings" in md_report
    assert "Code Context Slice" in md_report
    assert "Model Generation" in md_report

    # Dict export
    trace_dict = trace.to_dict()
    assert trace_dict["trace_id"] == trace.trace_id
    assert trace_dict["served_tier"] == trace.served_tier
    assert trace_dict["prompt_tokens"] == 80


@pytest.mark.asyncio
async def test_agent_harness_multi_trace_collection(sample_code_file: Path, tmp_path: Path):
    """Verify multiple executions append traces and can be cleared."""
    server = create_server(ledger_path=tmp_path / "multi_ledger.json")
    config = BackendConfig.llama_server()
    client = make_mock_llm_client(config=config)
    harness = AgentHarness(backend_config=config, server=server, client=client)

    await harness.execute_task(str(sample_code_file), "Inspect PaymentGateway", "PaymentGateway")
    await harness.execute_task(str(sample_code_file), "Check verify_signature", "verify_signature")

    traces = harness.get_traces()
    assert len(traces) == 2

    harness.clear_traces()
    assert len(harness.get_traces()) == 0


@pytest.mark.asyncio
async def test_agent_harness_security_violation_passthrough(tmp_path: Path):
    """Verify that targeting protected paths raises SecurityViolationError through the harness."""
    server = create_server(ledger_path=tmp_path / "sec_ledger.json")
    config = BackendConfig.llama_server()
    client = make_mock_llm_client(config=config)
    harness = AgentHarness(backend_config=config, server=server, client=client)

    with pytest.raises(Exception) as exc_info:
        await harness.execute_task(
            file_path=".env",
            task_intent="Inspect database secret credentials",
        )

    cause = exc_info.value.__cause__ or exc_info.value
    assert isinstance(cause, SecurityViolationError)
