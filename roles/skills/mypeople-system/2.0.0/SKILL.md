---
name: mypeople-system
description: Mandatory MyPeople board, messaging, agent-state, and owner-lifecycle operating contract.
metadata:
  version: 2.0.0
  load: startup
  required: true
---

# Operating MyPeople

This skill is mandatory startup knowledge. The TODO card is the human-visible source of truth; a
terminal-only report is not a report.

## Identity and environment

- Your author identity is the full `$AGENT_ID` (`<host>/<session>:<tab>`). Never post as `CEO` or as
  another agent.
- `$QUEUE_URL` is the board/queue origin and `$QUEUE_SECRET` is the machine credential. Never print,
  store in artifacts, or paste the secret into a prompt, roster, command report, or card comment.
- `$BOSS_ID` is the upstream agent for queue notifications when present.

## Read and report on the card

Read current board state before acting:

```sh
curl -sf -H "X-Queue-Secret: $QUEUE_SECRET" "$QUEUE_URL/todo/board"
```

Post the understanding handshake, plan, meaningful progress, blockers, and final evidence to the
assigned card under your own identity:

```sh
jq -n --arg task_id "$TASK_ID" --arg by "$AGENT_ID" --arg body "$BODY" \
  '{task_id:$task_id,by:$by,body:$body}' |
curl -sf -X POST -H "X-Queue-Secret: $QUEUE_SECRET" \
  -H 'Content-Type: application/json' --data-binary @- "$QUEUE_URL/todo/comment"
```

Use `POST /todo/proof {task_id,kind,url|body}` for durable proof. Do not mark a card done unless the
delegation grants that authority and its done condition is actually proven.

## Supported agent operations

- `mp status` — query agents and nodes.
- `mp send <agent_id> <message>` — deliver a message through MyPeople.
- `mp peek <agent_id>` — inspect the live pane through the supported interface.
- `mp spawn <agent_id> ...` — create an agent only when delegated and with the required role/lifecycle
  flags.
- `mp answer <agent_id> <N>` — answer a pending choice.
- `mp kill <agent_id> --reason <text>` — retire only an agent you are authorized to retire.
- `mp revive <agent_id>` — resume the recorded backend session and locked role.

Never bypass these operations with ad-hoc raw tmux delivery. A role may teach an operation while its
policy still limits whether and where you can perform it.

## Owner lifecycle

One open TODO card has one lifetime owner. An owner is spawned with `--owner-task <card_id>` and
remains responsible across every CEO follow-up and every completed turn until the CEO closes the
card. Do not spawn a replacement for a follow-up. `--temporary` agents cannot own cards. Closing a
card retires its owner; reopening requires a fresh explicit owner assignment.

When blocked, post the concrete blocker and the smallest decision or external change needed. When
finished, post commands, results, file/commit references, and limitations on the card; then wait for
follow-ups while remaining owner.
