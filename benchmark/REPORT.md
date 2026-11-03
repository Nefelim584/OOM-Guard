# ACC-MCP Benchmark Suite: Baseline vs. Gated Comparison (Block 9)

## 1. Executive Summary & Comparative Totals
- **Experimental Scale:** 10 tasks $\times$ 3 runs each = **60 total runs** (30 Baseline, 30 Gated)
- **Context Window Ceiling ($C_{\text{window}}$):** 8192 tokens
- **Overall Mean Token Consumption:**
  - **Baseline (Raw Full Files):** 2251.4 tokens (Std Dev: $\pm$6055.9, Var: 36674312.7)
  - **Gated (ACC-MCP):** 191.6 tokens (Std Dev: $\pm$97.8, Var: 9572.1)
- **Overall Relative Token Reduction:** **91.49%**
- **Task Completion Success Rate:**
  - **Baseline:** **90.0%** (Failed on oversized codebase due to context overflow)
  - **Gated (ACC-MCP):** **100.0%** (Zero failures, hard ceiling strictly enforced)

## 2. Per-Task Comparative Breakdown (Averaged over 3 Runs Each)
| Task ID | Title | Category | Baseline Mean | Baseline Spread | Gated Mean | Gated Spread | Savings (%) | Baseline Success | Gated Success |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `task_01` | Auth Token Verification | small | 94 | $\pm$0.0 | 68 | $\pm$0.0 | **27.7%** | 100% | **100%** |
| `task_02` | Variance Metric Helper | small | 160 | $\pm$0.0 | 62 | $\pm$0.0 | **61.2%** | 100% | **100%** |
| `task_03` | Config Loader Structure | small | 265 | $\pm$0.0 | 159 | $\pm$0.0 | **40.0%** | 100% | **100%** |
| `task_04` | Event Emitter Dispatch Refactor | medium | 270 | $\pm$0.0 | 245 | $\pm$0.0 | **9.3%** | 100% | **100%** |
| `task_05` | Filter Clause Builder Tracing | medium | 276 | $\pm$0.0 | 211 | $\pm$0.0 | **23.6%** | 100% | **100%** |
| `task_06` | Cache Prefix Invalidation Deprecation | medium | 275 | $\pm$0.0 | 253 | $\pm$0.0 | **8.0%** | 100% | **100%** |
| `task_07` | Order Pipeline Transition Logic | large | 429 | $\pm$0.0 | 352 | $\pm$0.0 | **17.9%** | 100% | **100%** |
| `task_08` | Stream Partitioner Execution Loop | large | 323 | $\pm$0.0 | 244 | $\pm$0.0 | **24.5%** | 100% | **100%** |
| `task_09` | Crypto Ledger Merkle Calculation | large | 310 | $\pm$0.0 | 266 | $\pm$0.0 | **14.2%** | 100% | **100%** |
| `task_10` | Enterprise Monolith Health Check | oversized | 20112 | $\pm$0.0 | 56 | $\pm$0.0 | **99.7%** | 0% | **100%** |

## 3. Detailed Trace Analysis: Baseline Context Overflow Failure vs. Gated Success
- **Target Task:** `task_10` — *Enterprise Monolith Health Check*
- **Task Intent:** *"Check health_check endpoint signature and status code"*
- **Model Context Window Limit:** 8192 tokens

### Baseline Failure Trace:
```text
[ERROR] ContextWindowOverflowError: Prompt tokens (20112) exceeded model context window limit (8192 tokens).
Raw file token footprint: 20090 tokens.
Prompt payload size: 20112 tokens.
Result: The agent fails immediately with context overflow; prompt instructions are discarded.
```

### Gated (ACC-MCP) Success Trace:
- **Pruned AST Payload:** Only **56 tokens** served (a **99.72%** reduction!).
- **Delivered Context Slice:**
```python
def health_check(verbose: bool=False) -> dict[str, str]:
    """Return microservice health status dictionary with 200 OK semantics."""
    ...
```
- **Verdict:** ACC-MCP extracted only the targeted interface boundary, staying comfortably within the token ceiling and allowing the LLM to complete the query flawlessly.