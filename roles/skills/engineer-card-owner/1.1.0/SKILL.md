---
name: engineer-card-owner
description: Engineer-only card brainstorming, execution, evidence, verification, and blocker-escalation workflow.
metadata:
  version: 1.1.0
  load: startup
  required: true
---

# Engineer card-owner workflow

Start by reading the full card and sending the required understanding handshake and implementation
plan. Preserve unrelated work, implement only the delegated scope, and verify in proportion to risk.

## Mandatory brainstorm gate

Before any implementation work on a card in `needs_brainstorm`, invoke the existing **Superpowers
brainstorming** skill and complete its design-and-approval workflow. Then translate the approved
design into a concrete, testable `doneCondition`, persist it on the assigned card, and read the card
back to confirm it is non-empty. Only then may you move the card to `working` and begin implementation.
The done condition records the approved outcome; writing one is not a substitute for running the
skill. If the Superpowers brainstorming skill is unavailable, report that as a blocker on the card
and to the Boss. Never invent or silently substitute another brainstorming workflow, and you must
not move the card to `working` or begin implementation while blocked.

Report durable progress while working. A blocker report names the failing gate, evidence, attempted
safe alternatives, and the exact help needed. A final report leads with the outcome and includes
tests, live proof, materialized artifacts, commit/push state, and any known limitation. Do not close
the card or abandon ownership merely because one implementation turn is complete.
