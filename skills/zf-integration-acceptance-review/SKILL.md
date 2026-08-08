---
name: zf-integration-acceptance-review
description: "Bounded read-only semantic review for an admitted high/critical Task after independent Verify and before incremental Candidate integration."
stages: [acceptance_review]
tags: [task-pipeline, integration, risk, review]
dependencies: []
auto_inject: false
load_on_demand: true
---

# ZaoFu Integration Acceptance Review

Use this Skill only when the briefing identifies a current
`risk_review` Task Pipeline operation. The Kernel has already admitted the
Task Contract, exact target, and independent Task Verify result.

## Required Method

1. Read every Required Read source with the sanctioned artifact-read command.
2. Confirm the Verify result is for the exact Task Contract revision and target.
3. Assess only residual integration risk introduced by this Task.
4. Return one typed verdict:
   - `admit`: no Task-local integration blocker remains;
   - `revise`: provide concrete Task-local typed feedback;
   - `replan`: provide a bounded graph/task-map delta intent for OA/Planner;
   - `block`: provide a typed blocker that requires controlled resolution.
5. Cite durable evidence and state residual risks explicitly.

## Authority Boundary

This reviewer is read-only. Do not edit code, run product tests, change git
refs, mutate TaskStore/Candidate/runtime state, create tasks, or emit terminal
truth. Do not decide global Goal closure, Candidate parity, or shipping. The
Kernel validates identity/evidence/currentness and applies the mechanical route.
