# Phase 6 Prompt — Human-in-the-loop [STRONG MODEL]

## Goal

Prove that risky actions pause, require authorized human decision, and resume safely without duplicate effects.

## Implement

- risk policy L0–L3
- approval model/repository/service/API
- sanitized proposed action display
- LangGraph interrupt/pause integration
- checkpoint persistence
- approve/reject
- resume
- audit
- idempotency for approved side-effect adapter

## Use a mock/dry-run external action first

Do not send real email or mutate real third-party systems for the first implementation.

## Mandatory tests

1. L0 executes without approval.
2. L2 creates WAITING_APPROVAL.
3. tool effect count is zero before approval.
4. authorized approval resumes.
5. effect count becomes exactly one.
6. duplicate approve does not create second effect.
7. reject creates zero effects.
8. unauthorized approval denied.
9. tampering with client-side proposed args cannot change the server-persisted action.
10. ambiguous tool failure does not blindly repeat an external action.

Update `SECURITY_HITL.md` and progress log.
