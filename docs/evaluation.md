# Evaluation: how to measure the investigator

> Status: Phase 7 (2026-11-23). Code: [`evals/`](../evals/). Dataset:
> [`evals/datasets/incidents.json`](../evals/datasets/incidents.json). Results:
> [`evals/results/`](../evals/results/). Dashboard: **Evaluations** in the web console.
> Design rationale: [evaluation-plan.md](evaluation-plan.md).

## Run it

```bash
# all rule-based configurations on all scenarios (deterministic, no LLM tokens, ~10 s)
uv run python -m evaluation.run --dataset evals/datasets/incidents.json

# list scenarios and configurations
uv run python -m evaluation.run --list

# selected configurations / scenarios
uv run python -m evaluation.run --config rules-baseline --config rules-no-knowledge \
    --scenario S01-orders-n-plus-one-deploy --scenario S18-vague-slowness-no-cause

# LLM configurations on a subset, with the model-based judge (uses tokens)
uv run python -m evaluation.run --config rules-baseline --config llm-planner \
    --llm-scenarios S04-db-network-latency,S14-payments-with-late-deploy \
    --judge --judge-configs rules-baseline,llm-planner --llm-delay 4
```

Each run writes `evals/results/<run_id>/results.json` (every scored investigation plus
aggregates) and `report.md` (comparison tables, per-tag and per-scenario results, misses,
limitations), and adds the run to `evals/results/index.json`, which the dashboard reads.

## How it works

```
dataset scenario ──► WorldGateway (faults → telemetry → MCP-shaped tool results)
                          │
agent configuration ──► the real LangGraph investigation graph (same code as production)
                          │
                 report + evidence + OpenTelemetry trace
                          │
             scoring (programmatic) [+ model-based judge, optional] ──► results.json / report.md
```

- **Same agent, different world.** The harness compiles the production graph
  (`ai_sre.agent.graph.build_graph`) with a `WorldGateway` instead of the MCP client. Nothing in
  the agent knows it is being evaluated. Remediation is never executed; proposals are scored.
- **Scenario worlds** (`ai_sre_evals/world.py`). A scenario declares the injected faults (the
  simulator's fault types), deployments and optional overrides. `Telemetry` derives what each
  fault makes observable: metric anomalies and their onsets, error signatures and log lines,
  pool saturation, database sessions and slow queries, client-side query latency, dependency and
  service health, memory growth, queue depth, knowledge-base hits. `WorldGateway` answers every
  read-only tool from that model with the payload shapes of the real MCP servers, including
  `compare_metric_windows` (a signal "rises" at a deploy only if the fault started after it).
  It also simulates **missing** tools (`unavailable`: fails, retried once) and **flaky** tools
  (`flaky`: first attempt fails, retry succeeds).
- **Why not the live simulator?** A live run takes minutes per scenario and its timing varies,
  so runs are not comparable, and several situations (missing telemetry, simultaneous faults,
  conflicting signals) cannot be produced reliably. Worlds are reproducible to the byte and fast.
  Scenarios that correspond to a predefined simulator scenario name it in
  `injected_fault.sim_scenario`, so they can also be reproduced live with `simctl trigger <id>`.

## The dataset

25 scenarios (`evals/datasets/incidents.json`, validated by `ai_sre_evals.dataset`):

| Situation | Scenarios |
|-----------|-----------|
| Recent deployment correlation | S01, S05–S08, S12, S14, S15, S17, S19–S21, S23, S24 |
| Database bottlenecks | S01, S04, S05, S11, S12, S15, S20, S23 |
| Dependency failures (payments) | S03, S14, S16, S22 |
| Redis failures | S02, S13, S16, S21, S25 |
| Queue failures | S02, S08, S09, S17 |
| Misleading symptoms | S01, S04, S10, S14, S15, S19–S21 |
| Missing evidence | S12 (logs + database down), S13 (infrastructure + anomaly detector down) |
| Conflicting evidence | S14 (deploy after onset), S15 (gateway release vs orders leak), S21 (deploy 1 min before a Redis outage) |
| Multiple simultaneous failures | S16 (Redis + payments), S17 (memory leak + worker backlog) |
| No obvious root cause | S18 (vague slowness), S19 (innocent deploy blamed) |

Each scenario defines:

| Field | Meaning |
|-------|---------|
| `id`, `title`, `description` | Scenario id and the incident story (what on-call sees) |
| `query` | The question asked to the agent |
| `tags` | Situation types (used for per-tag results) |
| `injected_fault` | `faults` (type, target, params, onset, `caused_by` deployment), `deployments`, optional `sim_scenario` |
| `world` | `unavailable` / `flaky` tools, `overrides` (extra anomalies/errors, dependency or pool state, knowledge hits), `knowledge_confident` |
| `expected.root_cause` | category, component, trigger deployment (or `null`: the agent should abstain) |
| `expected.also_correct` | other true causes when several failures overlap (full credit) |
| `expected.acceptable_alternatives` | the symptom rather than the trigger (half credit) |
| `expected.affected_services` | services impacted |
| `expected.evidence` | `server.tool` keys whose results prove the cause |
| `expected.tools` | tools a competent investigation consults |
| `expected.remediation` | correct first action (+ target service or required keywords) |
| `expected.unsafe_actions` | actions that would be wrong as the first step |
| `expected.severity` | SEV1–SEV4 from user impact |

### Add a scenario

1. Pick the fault(s) from `simctl fault-types` (same names) and write the story.
2. Append an entry to `evals/datasets/incidents.json`:

```json
{
  "id": "S26-orders-latency-after-config",
  "title": "Orders latency after a timeout config change",
  "description": "A config change lowers the DB statement timeout; order lookups slow down and time out.",
  "query": "Order lookups got slow in the last 20 minutes",
  "tags": ["recent_deployment", "database_bottleneck"],
  "injected_fault": {
    "sim_scenario": null,
    "faults": [{"type": "slow_db_queries", "target": "orders-service", "onset_minutes_ago": 9,
                "caused_by": "d-700", "params": {}}],
    "deployments": [{"id": "d-700", "service": "orders-service", "kind": "config_change",
                     "version": "2.3.1-cfg.9", "previous_version": "2.3.1", "minutes_ago": 10,
                     "changes_summary": "Lower statement_timeout to 200ms"}]
  },
  "world": {},
  "expected": {
    "root_cause": {"category": "config_change", "component": "orders-service", "trigger_ref": "d-700"},
    "acceptable_alternatives": [{"category": "database_degradation", "component": "postgres"}],
    "affected_services": ["orders-service", "api-gateway"],
    "evidence": ["deployments.list_recent_deployments", "database.get_connection_pool_status"],
    "tools": ["metrics.detect_metric_anomalies", "deployments.list_recent_deployments",
              "logs.aggregate_errors", "infrastructure.get_dependency_status"],
    "remediation": {"action": "rollback_deployment_simulation", "target": "orders-service", "keywords": []},
    "unsafe_actions": [],
    "severity": "SEV3"
  }
}
```

3. Validate and look at what the world produces: `uv run python -m evaluation.run --list`
   (fails loudly on schema errors or unknown deployment ids), then run just that scenario with
   `--scenario S26-...` and read the report. If a new fault type is needed, add a
   `_fault_<type>` method to `Telemetry` in `world.py`.
4. Write the ground truth **before** looking at the agent's answer. Do not change it to match
   the agent; change it only if the scenario itself is inconsistent (record why in the commit).

## Metrics

All programmatic, per investigation, then averaged (`ai_sre_evals/scoring.py`):

| Metric | Definition |
|--------|------------|
| Root-cause accuracy | 1 if (category, component, trigger) match `root_cause` or `also_correct`; 0.5 for an acceptable alternative; 0 otherwise. Abstention scenarios: 1 if no root cause or only low/insufficient confidence |
| Root cause in top 3 | expected cause among the root cause and the first two alternatives |
| Evidence precision / recall | tools behind the evidence the root cause cites vs `expected.evidence` |
| Retrieval recall | `expected.evidence` tools that were called successfully |
| Tool selection accuracy | share of `expected.tools` the agent called |
| Tool call efficiency | share of calls that were to expected tools (detours and duplicates lower it) |
| Severity accuracy | reported severity equals expected |
| Remediation accuracy | first proposal has the expected action/target/keywords and is not unsafe |
| Hallucination rate | share of investigations with at least one ungrounded claim: root-cause component, trigger deployment, alternative components, affected services not present in any tool result; cited evidence ids that do not exist; knowledge citations not returned by a knowledge tool |
| Completion rate | a report was produced without a node error or timeout |
| Latency, tokens, cost, LLM fallback | from the investigation's OpenTelemetry trace ([observability.md](observability.md)) |

## Configurations compared

| Config | What changes |
|--------|--------------|
| `rules-baseline` | rule-based planner, default limits |
| `rules-single-round` | triage only (`max_rounds=1`) |
| `rules-eager-stop` | stop at confidence 0.55 instead of 0.75 |
| `rules-no-knowledge` | ablation: knowledge base and incident history unavailable |
| `rules-tight-budget` | at most 10 tool calls |
| `llm-planner` | Gemini intake and planning (default prompt) |
| `llm-planner-hypothesis-first` | Gemini planning with a "disprove each hypothesis" prompt |

Add one in `ai_sre_evals/configs.py` (reasoner, prompt variant, `Limits`, disabled tools).

## Model-based judge

`--judge` asks the LLM to grade each report against the ground truth: verdict
(correct / partially correct / incorrect / abstained), explanation quality and actionability
(1–5) and the number of unsupported statements. The judge sees the scenario description, the
ground truth and the report summary, not raw telemetry. The run reports its agreement with the
programmatic verdict.

**Use it as a second opinion only.** Correctness is decided by the program. The judge is one
model, sampled once, with no calibration set; its scores are ordinal opinions, it can be
lenient towards fluent text, and it costs tokens.

## Results (2026-11-23)

Two committed runs. Both were made from the uncommitted Phase 7 working tree on top of commit
`d794617`; later runs record a `-dirty` suffix when that happens.

**Run `20261123-144128`: first measurement** (rule-based configurations, 25 scenarios):

| Config | RC acc | Ev. recall | Severity | Remediation | Halluc. | Calls |
|---|---|---|---|---|---|---|
| `rules-baseline` | 92% | 80% | 76% | 88% | 0% | 14.0 |

It found two agent defects:
- S04 (network latency to PostgreSQL) and S11 (query-plan regression without a deploy) got no
  root cause. Without a deployment, triage produced no hypothesis, so no database checks were
  planned.
- "started returning 500s" was not recognised as an error symptom (S24 severity SEV4).

Both were fixed: an exploratory database/resource round when no hypothesis forms, and the
symptom regex.

**Run `20261123-151159`: after the fixes** ([report](../evals/results/20261123-151159/report.md)):

| Config | n | RC acc | Ev. prec | Ev. recall | Tool sel. | Tool eff. | Severity | Remediation | Halluc. | Complete | Calls | Latency | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `rules-baseline` | 25 | 100% | 59% | 89% | 93% | 40% | 72% | 96% | 0% | 100% | 14.6 | 85 ms | 0 | $0 |
| `rules-single-round` | 25 | 88% | 60% | 70% | 77% | 45% | 80% | 84% | 0% | 100% | 10.5 | 53 ms | 0 | $0 |
| `rules-eager-stop` | 25 | 100% | 60% | 83% | 84% | 42% | 72% | 96% | 0% | 100% | 12.4 | 77 ms | 0 | $0 |
| `rules-no-knowledge` | 25 | 100% | 58% | 94% | 100% | 40% | 72% | 96% | 0% | 100% | 16.0 | 140 ms | 0 | $0 |
| `rules-tight-budget` | 25 | 92% | 58% | 72% | 85% | 44% | 80% | 88% | 0% | 100% | 11.6 | 82 ms | 0 | $0 |

Head to head on the 6 hardest scenarios (S04, S11, S14, S16, S19, S21), the only fair
comparison with the LLM configurations:

| Config | RC acc | Severity | Remediation | Ev. recall | Calls | Latency | Tokens | LLM fallback | Cost (6 runs) |
|---|---|---|---|---|---|---|---|---|---|
| `rules-baseline` | 100% | 83% | 100% | 100% | 16.3 | 99 ms | 0 | — | $0 |
| `rules-single-round` | 67% | 100% | 67% | 60% | 9.3 | 50 ms | 0 | — | $0 |
| `llm-planner` | 100% | 67% | 100% | 100% | 17.0 | 47.4 s | 709 | 53% | $0.0010 |
| `llm-planner-hypothesis-first` | 100% | 50% | 100% | 100% | 18.7 | 31.7 s | 997 | 28% | $0.0013 |

What this says, and what it does not:

- **Follow-up rounds matter.** Triage alone (`rules-single-round`) loses 12 points of root-cause
  accuracy and 19 points of evidence recall overall, and a third of the hard cases. A 10-call
  budget (`rules-tight-budget`) costs a similar amount.
- **Stopping earlier is free here.** `rules-eager-stop` keeps accuracy with ~15% fewer calls.
  The worlds give clean signals; with noisier data an earlier stop is riskier.
- **The knowledge base does not change answers on this dataset.** Without it, accuracy is the
  same, while calls, recall and latency go up (the agent gathers more first-party evidence).
  Its value here is citations and runbooks in the report, not accuracy.
- **Severity is the weakest output** (72%). It comes from the wording of the question, plus a
  bump to SEV2 when a confident cause spans two services. The bump over-escalates (S04, S20,
  S25), the wording misses impact (S06, S08, S24) and over-rates a vague report (S18). This is
  the clearest improvement target.
- **Remediation misses only S10** (traffic surge): the action catalogue has no scale-out for the
  gateway, so the agent proposes "investigate further". A catalogue gap, not a diagnosis error.
- **Evidence precision is low (~59%)** by construction: the rubric cites every supporting
  finding, including symptoms, not only the decisive evidence.
- **The LLM planner shows no measurable gain, and the measurement is weak.** Gemini returned
  "503 high demand" for 53% / 28% of calls; those steps fell back to the rule planner (visible in
  the traces). Accuracy equals the rule planner on these 6 scenarios, severity is worse
  (intake from the LLM), and latency is ~300–500× higher at ~$0.0002 per investigation. A fair
  LLM comparison needs a stable provider, more scenarios and repeated samples.
- **No hallucinations were detected** (0 ungrounded structured claims). This is expected: the
  report is assembled from findings, and the LLM only plans. It is a property of the design,
  not evidence that an LLM would not hallucinate.
- **Judge** (5 judged reports each for `rules-baseline` and `llm-planner`): 80% agreement with
  the program; explanation quality 4.4 / 4.6 out of 5. Of the two disagreements, one was a
  harness bug (the judge prompt omitted the second true cause of S16; fixed in `judge.py`) and
  one a defensible difference (S11: the judge called "slow queries on PostgreSQL" a symptom).

## Limitations (read before quoting numbers)

- **Synthetic worlds.** Telemetry is generated from fault models. It is consistent and
  realistic in shape but much less noisy than production: real incidents have unrelated
  anomalies, partial outages and missing labels.
- **In-sample tuning.** The rule-based agent was built alongside the simulator scenarios that
  S01–S09 mirror, and the first evaluation run (`20261123-144128`) led to two agent fixes
  (exploratory database checks when triage finds no hypothesis, and recognising "500s" as an
  error symptom). Later runs therefore score the agent on data that influenced it. Treat high
  scores as an upper bound and add new scenarios to measure generalisation.
- **Severity** comes mostly from the wording of the question, so it measures intake, not impact
  assessment.
- **Latency** in replay mode is agent compute (plus LLM calls); tool calls are answered in
  process, so telemetry-backend latency is not included.
- **LLM runs** use a 6-scenario subset (free-tier token limits), one sample each; provider
  errors make the reasoner fall back to rules for that step (see "LLM fallback"), so LLM numbers
  partly reflect the rule planner. Compare LLM configurations only in the head-to-head table.
- **Hallucination** is measured on structured claims, not on free-text wording.
