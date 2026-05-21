# Finance + Sales Agent — Group 5
**Team**: Aarav Gumber (Lead) · Saketh Madiraju · Nimeshikaa Saravanakumar

AI Enterprise Agent Program — 12-Week Project

---

## Quick Start

```bash
# Install deps
pip install -r requirements.txt

# Demo mode (no API key needed — uses real tools, stubs LLM)
python main.py demo

# Live mode (real Anthropic API calls)
export ANTHROPIC_API_KEY=sk-...
python main.py run

# Check DB status
python main.py status

# Run tests
python main.py test
# or: python -m pytest tests/ -v
```

---

## Project Structure

```
finance_sales_agent/
├── schema.py              # Mandatory inter-agent message envelope + TokenUsage
├── main.py                # Entry point (demo / run / status / test)
├── requirements.txt
├── agents/
│   ├── base_agent.py      # Base class: LLM loop, token tracking, bus integration
│   ├── finance_agent.py   # Finance Agent — handles P&L, budgets, forecasts, alerts
│   └── sales_agent.py     # Sales Agent — qualifies leads, pitches, closes, upsells
├── tools/
│   ├── finance_tools.py   # Budget calc, Monte Carlo, P&L generator, SQLite ledger
│   └── sales_tools.py     # CRM sim, lead scorer, pitch templates, pipeline tracker
├── bus/
│   └── message_bus.py     # Async in-memory pub/sub bus (Redis-compatible interface)
├── tests/
│   └── test_agents.py     # Unit + integration + chaos tests
├── db/                    # SQLite databases (auto-created)
└── logs/                  # Session JSON logs
```

---

## Message Schema (mandatory for all agents)

```json
{
  "id": "<uuid>",
  "timestamp": "<iso8601>",
  "sender": "<agent_name>",
  "recipient": "<agent_name | broadcast>",
  "task_type": "<enum>",
  "context": {},
  "payload": {},
  "status": "pending | in_progress | done | error",
  "error": "",
  "token_usage": {
    "input_tokens": 612,
    "output_tokens": 388,
    "total_tokens": 1000,
    "cost_usd": 0.0031,
    "model": "claude-sonnet-4-20250514"
  }
}
```

`token_usage` is filled by the sending agent AFTER the LLM call completes.
Max budget: **1,000 tokens per call**. All calls log to `db/finance.db:token_costs`.

---

## Finance Agent Task Types

| Task | Description |
|---|---|
| `GENERATE_PL_REPORT` | Build P&L for a quarter from ledger |
| `BUDGET_APPROVAL` | Approve/escalate budget requests (>$10K → CEO) |
| `CASH_FLOW_FORECAST` | Monte Carlo simulation (P10/P50/P90) |
| `REVENUE_LOG` | Accept closed-deal revenue from Sales |
| `AUDIT_REPORT` | Full transaction audit with anomaly detection |
| `MONTE_CARLO_SIM` | Raw simulation endpoint |
| `BUDGET_ALERT` | Outbound alert to CEO when burn >75% |

## Sales Agent Task Types

| Task | Description |
|---|---|
| `QUALIFY_LEAD` | BANT scoring → qualified/not qualified |
| `GENERATE_PITCH` | Personalized pitch per segment + objection handling |
| `CLOSE_DEAL` | Mark won/lost; auto-sends REVENUE_LOG to Finance |
| `UPSELL` | Find and prioritize upsell opportunities |
| `PIPELINE_REPORT` | Stage counts, value, conversion summary |
| `DEMO_REQUEST` | Generate 30-min demo agenda |

---

## Token Budget

- `max_tokens = 1000` enforced on every API call
- Allocation: ~300 system prompt + ~200 payload + ~300 RAG context + ~200 output
- Pricing (Sonnet 4): $3/M input tokens, $15/M output tokens
- All calls logged to SQLite with: `input_tokens, output_tokens, total_tokens, cost_usd`
- Session totals tracked in `MessageBus.get_session_stats()`

---

## Decision Rules

1. Expenses/allocations **>$10,000** → auto-escalate to CEO before proceeding
2. Burn rate **>75%** of quarterly budget → BUDGET_ALERT sent to CEO immediately
3. Every `closed_won` deal → Sales auto-sends `REVENUE_LOG` to Finance
4. Leads with qualification score **<50** → deprioritized
5. Deals **>$50,000** → Sales escalates to CEO for final sign-off

---

## Week-by-Week Progress

| Week | Deliverable | Status |
|---|---|---|
| 1–2 | Role mapping, one-pager | ✅ |
| 3 | Design doc + schema | ✅ |
| 4 | Pseudocode + repo setup | ✅ |
| 5 | Skeleton with stub tools | ✅ |
| 6 | Real tools (4/2/2026) | ✅ |
| 7 | Message bus + CEO routing | ✅ |
| 8 | Full scenario run | 🔄 |
| 9 | Tests + metrics | ✅ |
| 10 | Chaos tests + reliability | ✅ |
| 11 | Stretch: UI dashboard | 🔄 |
| 12 | Final demo | ⏳ |

---

## Security / Access Control

- Finance: reads revenue logs, expenses, budgets. **Cannot** access HR data.
- Sales: reads leads, product specs, pricing. **Cannot** access internal budget details.
- Role enforcement in `BaseAgent` — each agent only subscribes to its own queue.

## Contact
Discord: https://discord.gg/qdMXeB8y
Meeting: https://meet.google.com/hep-peif-ezo
