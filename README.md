
## Overview
This repo contains the Finance and Sales agents for the Kanosei virtual enterprise simulation. These agents communicate with a CEO agent and each other using a shared JSON message format to run a virtual company.

## Framework
We chose **CrewAI** as our framework. A framework is basically a set of pre-built tools and rules that helps organize how everything works together. CrewAI was the best fit because it's already built around roles and tasks, which is exactly how our system is set up: a CEO agent that hands off work to Finance, Sales, and other agents.

## Agents

### Finance Agent
**Responsibilities:** Track budgets, approve/deny spend requests, generate P&L reports, escalate transactions over $10K to the CEO.

**Communicates with:**
- CEO – sends budget reports, escalates high-value spend
- Sales – receives closed deal revenue, sends budget alerts
- HR – approves or denies hiring/salary spend requests

**Task types it handles:**
- `APPROVE_SPEND` – auto-approves under $10K, escalates over $10K to CEO
- `LOG_EXPENSE` – saves a record of money spent
- `GENERATE_PL` – calculates and sends a profit/loss report to CEO
- `BURN_RATE_ALERT` – estimates runway in months, warns if under 3 months
- `ALLOCATE_BUDGET` – stores budget amounts per department
- `VALIDATE_REVENUE` – cross-checks revenue numbers from Sales

**Tools:** budget calculator, SQLite database (expenses & revenue), P&L report builder, cash flow simulator (3–6 month runway projection), burn rate alert system

### Sales Agent
**Responsibilities:** Manage deal pipeline, run customer outreach, report revenue forecasts, surface customer feedback.

**Communicates with:**
- CEO – sends weekly pipeline reports, escalates non-standard deals
- Finance – reports closed deal values, submits revenue forecasts
- Product Manager – forwards customer feedback and feature requests

**Task types it handles:**
- `QUALIFY_LEAD` – scores leads by deal size and fit, saves to database
- `GENERATE_PITCH` – uses an LLM to write personalized outreach messages
- `LOG_REVENUE` – records closed deals and notifies Finance automatically
- `PIPELINE_REPORT` – sends CEO a summary of all active deals
- `REVENUE_FORECAST` – estimates monthly/quarterly revenue from current pipeline
- `CUSTOMER_FEEDBACK` – forwards feedback and feature requests to Product Manager
- `ESCALATE_DISCOUNT` – auto-approves discounts under 20%, escalates over 20% to CEO

**Tools:** lead scorer, LLM-powered message writer, SQLite database (leads & deals), closed deal notifier (sends to Finance), objection handler

## Agent API
Both agents are built the same way so the CEO can talk to either one using the exact same approach. Every agent follows a three-step loop:

1. **Receive** a message and figure out what kind of task it is
2. **Handle** the task using whatever tools it needs
3. **Respond** with the result wrapped in the standard JSON envelope

### Message Format
All inter-agent messages use this shared JSON schema:
```json
{
  "id": "<uuid>",
  "timestamp": "<iso8601>",
  "sender": "<agent_name>",
  "recipient": "<agent_name>",
  "task_type": "<task>",
  "context": {},
  "payload": {},
  "status": "<pending|in_progress|done|error>",
  "error": ""
}
```
---
