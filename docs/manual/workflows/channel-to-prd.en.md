# Channel To PRD

[中文](channel-to-prd.md) · [Workflow index](README.en.md)

> Current contract: a Channel is a requirement discussion room for people and
> agents. Natural conversation is the default; clarification and multi-lens work
> are explicit. Creating a Channel does not automatically fan out, and confirming
> a PRD does not create a Task or start a Workflow.

## When To Use It

Use a Channel when:

- a request has several plausible interpretations and needs owner, product, architecture, security, or critical perspectives;
- the result must return exactly to its Web, Feishu, or Kanban Agent origin;
- the team wants a canonical PRD before deciding whether to start delivery.

Do not use it when:

- a clear, small problem fits one agent session;
- a Task already exists and only needs a Workflow selection;
- chat members are expected to modify the Project or bypass approval.

## Three Product Modes

| Mode | Use it when... | Behavior |
|---|---|---|
| `conversation` | Default natural discussion and targeted mentions | The user or Leader chooses who responds; no automatic fanout |
| `clarification` | Fields, boundaries, or owner decisions are missing | Resolve open questions one by one |
| `multi_lens` | Several independent views and synthesis are explicitly needed | An explicit Discuss action runs bounded fanout and synthesis |

Compatibility engine names such as `manual_mention`, `mention_relay`, and
`fanout_then_synthesis` map into these modes. They are not additional top-level
product choices.

## Create A Channel

Ask Kanban Agent:

```text
Create a PRD discussion Channel for the login-security change.
Start with natural conversation and invite product, architecture, and security
perspectives. Do not create a Task or start a Workflow automatically.
```

Review the setup Plan for:

- template, Channel name, and Owner;
- required and optional members;
- provider/model overrides;
- discussion mode and budget;
- Leader, permissions, and allowed handoff;
- source/origin receipt target.

Creation materializes the Channel, Members, skills, permissions, and original
request. A default `conversation` does not fan out to all members merely because
the Channel now exists.

## Discuss And Converge

The normal path is:

```text
origin request
  -> durable message ACK
  -> natural conversation
  -> optional clarification
  -> optional explicit Discuss / multi_lens
  -> explicit Finalize
  -> PRD draft
  -> Continue | Revise | Owner confirm
  -> channel.consensus.reached(ref, digest, revision)
  -> exact-origin PRD receipt
```

![Animated Channel journey from natural discussion to multi-role convergence](../assets/quickstart-channel-discussion.webp)

Inside the discussion you can:

- mention one member for a focused answer;
- add constraints and continue the same thread;
- explicitly start multi-lens discussion;
- inspect member identity, provider, status, and permissions;
- request Finalize to create a PRD draft;
- continue, revise, or let the Owner confirm a revision.

Only an Owner-confirmed revision is the canonical PRD. A synthesis, an agent
summary, or verbal majority agreement does not replace Owner authority.

## Move From PRD To Delivery

The confirmed PRD still needs a separate handoff:

```text
confirmed PRD
  -> existing Task
     or Create Task from PRD proposal -> human confirm
  -> Task-bound Workflow Plan
  -> exact Workflow proposal
  -> Approve
  -> Kernel starts Workflow
  -> read-only Task/Run/Delivery receipt back to the original Channel
```

Only the exact `leader_member_id` with `propose_workflow` permission may create
the handoff proposal. The Leader still cannot approve its own proposal and must
not receive `ZF_WORKFLOW_ACTION_TOKEN`.

## Add A Message From CLI

The stable low-level message command is:

```bash
zf channel say CHANNEL-ID \
  --text "Add failure cases and ask @critic to verify them." \
  --member-id reviewer \
  --mention critic
```

Finalize, Owner confirm, member authority, and Workflow handoff currently use
Web/Kanban Agent, Feishu, or controlled-action surfaces. Do not simulate them by
editing `events.jsonl`.

## Observe And Diagnose

Check that:

- the origin message was admitted exactly once with an explicit ACK/NACK;
- the current product mode is expected;
- multi-lens work began only after an explicit action;
- PRD ref, digest, revision, and owner identity are complete;
- the receipt returned to the exact origin;
- Task/Workflow proposals bind the confirmed PRD revision;
- non-Leaders or unauthorized members cannot hand off;
- provider failures are deduplicated and remain recoverable.

```bash
zf events --last 100
zf status --workers
```

## Definition Of Done

Channel-to-PRD is complete when the Owner has confirmed a PRD with a ref,
digest, and revision and the origin has received its receipt. Delivery starts
only after a real Task exists, a Workflow is approved, and the Kernel executes
it.

## Related

- [Detailed Channel guide](../15-channel-collaboration.en.md)
- [Controlled Workflow start](controlled-workflow-start.en.md)
- [Feishu AI-Native Bridge](../19-feishu-ai-native-direct-bridge.en.md)
