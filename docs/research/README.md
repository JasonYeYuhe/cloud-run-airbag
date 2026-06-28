# Research that shaped Airbag

Before building, the idea was pressure-tested across **four models in parallel** (Claude,
Codex/GPT-5.5, Gemini 3.1 Pro, Gemini 3.5 Flash) over three rounds, then cross-verified.
The reports below are the synthesized conclusions; the raw per-model outputs are archived
under `raw-model-outputs/`.

| Round | Report | What it answered |
|---|---|---|
| 1 | [01-ideas-and-plan.md](01-ideas-and-plan.md) | What to submit + which project to build → converged on the autonomous self-heal agent |
| 2 | [02-competitive-analysis.md](02-competitive-analysis.md) | 42 verified competitors; the white space (out-of-window, action-layer, reversible); why we differ from Google's own Cloud Assist / Jules |
| 3 | [03-feasibility-and-market.md](03-feasibility-and-market.md) | Verified tech stack (ADK 1.x pin, Cloud Run rollback, the 5 landmines) + ICP / demand / pricing |

The competitive white space we targeted, in one line:

> Every auto-rollback tool only acts **inside the deploy window**; SRE tools only **diagnose**;
> coding agents only **open PRs offline**. Airbag closes the loop nobody else does — an
> **independent production alert → reversible Cloud Run rollback → proof error-rate = 0**.

`raw-model-outputs/` keeps each round's brief + every model's full answer (incl. the Claude
multi-agent web sweep JSON), so the reasoning trail is fully auditable.
