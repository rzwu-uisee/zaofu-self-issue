---
name: zf-research-fanout-trigger
description: "ZaoFu project-level trigger for starting the fixed-role research fanout workflow from a channel or Kanban Agent request. Use when the user explicitly invokes this skill, asks to trigger/start/run research fanout, wants a channel/Kanban Agent research request turned into a fixed multi-role fanout, or wants research results prepared for PRD/refactor prompt generation. Do not use for generic web searching or ordinary channel discussion that has not requested research fanout."
---

# ZaoFu Research Fanout Trigger

## Objective

Turn an explicit channel or Kanban Agent research request into a ZaoFu
controlled workflow trigger for the fixed-role research fanout template.

This skill is the entrypoint. The runtime fanout and subagents do the research.

## Ground Rules

- Treat this as a ZaoFu project skill, not a user-global research helper.
- Read `AGENTS.md` if you need repository rules or are about to run commands.
- Use `zf.yaml` as the only control-plane config. Do not hard-code `.zf`.
- Do not write `events.jsonl`, `kanban.json`, `session.yaml`, or other runtime
  truth files directly.
- Use `zf-workflow-start-planner` and the shared `zf workflow start` CLI.
- Do not silently run research from ordinary channel text. Require explicit
  skill invocation or an explicit user request for research fanout.
- Preparatory Research is Request-first. Create or reuse the exact Workflow
  Request revision before creating its workflow-managed Research Task.
- Treat the Workflow Request origin binding as the return authority. Channel
  ids and source refs are provenance and must not override that binding.
- If a required field, token, task, or workflow stage is missing, produce a
  preview payload and the exact blocker instead of bypassing the gate.

## Fixed Template

Use this template unless the user explicitly names another ZaoFu research
template:

```yaml
template_id: research-fanout.fixed.v1
pattern_id: research-fanout
roles:
  - source_researcher
  - product_analyst
  - technical_analyst
  - risk_critic
  - synthesizer
outputs:
  - research_summary
  - evidence_refs
  - open_questions
  - prd_prompt_input
  - refactor_prompt_input
```

Role intent:

- `source_researcher`: gather and cite primary or direct evidence.
- `product_analyst`: convert findings into user needs, scope, and PRD inputs.
- `technical_analyst`: identify architecture, implementation, and integration
  implications.
- `risk_critic`: challenge assumptions, missing evidence, rollout risk, and
  failure modes.
- `synthesizer`: produce the final research synthesis and PRD/refactor prompt
  inputs.

Runtime mapping: the first four roles run as `fanout.children`; `synthesizer`
runs as `aggregate.synth_role` after the child reports complete.

## Adaptive Pilot

Use `route_id=research:adaptive-pilot` only when the operator explicitly asks
for adaptive/provider-native Research and the active route catalog exposes it.
The route runs one ZaoFu `research_root`; that root may use bounded read-only
Provider children under `zf-research-adaptive-root`. It is an opt-in pilot, not
a declaration that Provider child telemetry or credential isolation is
production-qualified. If preflight rejects it, report the reason and offer
`research:fixed`; never silently relabel a fixed run as adaptive.

## Trigger Workflow

1. Classify and extract the request:
   - `topic`: the concrete thing to research.
   - `scope`: bounded aspects to investigate.
   - `expected_output`: default to
     `research synthesis plus PRD/refactor prompt inputs`.
   - `channel_id` and `thread_id`: identify the originating conversation when
     available.
   - classify it as preparatory when the result will update a later
     PRD/refactor/delivery request; otherwise it is standalone knowledge
     delivery.

2. For preparatory Research, create or reuse the Workflow Request through the
   controlled `workflow-request` action. Record its exact `request_id`,
   `request_revision`, and `origin_binding`. Do not start Research while the
   Request is missing or stale.

3. Create the Research Task through controlled `create-task` with:

```json
{
  "title": "<bounded research topic>",
  "execution_mode": "workflow",
  "request_id": "REQ-ID",
  "request_revision": 1
}
```

   A current workflow-managed Task may be reused only when its Request binding
   matches exactly. A standalone Research Task does not carry a Request and its
   result cannot be adopted into a later requirement.

4. Read and validate the active route:

   ```bash
   zf workflow routes --task TASK-ID --format json
   ```

   Select `route_id=research:fixed` for the stable audit path. Select
   `route_id=research:adaptive-pilot` only for an explicit adaptive pilot.
   The catalog owns the internal pattern, topology, roles, and rollout label.

5. Build the controlled action payload:

```json
{
  "task_id": "TASK-ID",
  "route_id": "research:fixed",
  "objective": "<research topic>",
  "request_id": "REQ-ID",
  "request_revision": 1,
  "parameters": {
    "topic": "<research topic>",
    "scope": ["<bounded aspect>"],
    "expected_output": "research synthesis plus PRD/refactor prompt inputs",
    "risk": "cost-bearing multi-agent research; keep evidence and open questions explicit"
  }
}
```

6. Create the exact proposal without requiring Web:

```bash
zf workflow start \
  --task TASK-ID \
  --route research:fixed \
  --objective "<research topic>" \
  --parameters-json '{"topic":"<research topic>","request_id":"REQ-ID","request_revision":1,"expected_output":"research synthesis plus PRD/refactor prompt inputs"}' \
  --propose \
  --format json
```

The proposal does not start research. Apply only through an explicitly
authorized operator path using its exact `proposal_event_id`.

7. Expected runtime sequence:

```text
skill trigger
-> Workflow Request current revision
-> workflow-managed Research Task bound to that revision
-> zf workflow routes
-> zf workflow start --propose
-> operator approval
-> zf workflow start --apply
-> workflow.invoke.requested
-> workflow.invoke.accepted
-> task.fanout.requested / fanout.requested
-> fixed-role research workers
-> fanout.aggregate.completed
-> workflow.result.available(ref + digest + lineage)
-> canonical origin return
-> explicit research-adopt
-> channel discussion/synthesis
-> PRD/refactor workflow prompt package
```

## Channel Reporting

After triggering, report these fields to the channel or user:

- `status`: requested, preview_only, or blocked.
- `route_id` and the catalog-projected template/topology.
- `task_id`, `channel_id`, and `thread_id`.
- `request_id`, `request_revision`, and the canonical origin surface.
- `workflow_run_id`, `workflow_input_manifest_ref`, and
  `workflow_prompt_ref` when the action returns them.
- `result_event_id`, artifact ref/digest, and whether the result is available
  or explicitly adopted.
- Any blocker, especially missing `task_id`, unavailable `research:fixed`,
  stale Task/config binding, or missing approval.

## Refusals

Do not execute if:

- the request is only a generic "research this" without explicit fanout intent;
- no task id exists and task creation is not authorized;
- preparatory Research has no current Workflow Request revision;
- the active catalog does not contain `research:fixed`;
- an adaptive pilot was requested but `research:adaptive-pilot` is unavailable
  or fails its read-only root preflight;
- the only possible path is direct mutation of runtime truth files;
- the user asks to skip gates, fabricate evidence, or hide cost/risk.
