# Phase 11 Prompt — Final Audit [STRONG MODEL]

Do not add features first. Review the actual repository.

## Audit categories

1. Product scope
2. Auth/tenant isolation
3. DB/state invariants
4. Agent graph
5. failure recovery
6. idempotency
7. HITL/approval bypass
8. context/injection boundaries
9. observability
10. evaluation quality
11. concurrency
12. docs accuracy
13. onboarding
14. tests/CI
15. license/upstream attribution

## Required outputs

- severity-ranked findings: Critical / High / Medium / Low
- exact file references
- reproduction/test where possible
- remediation task for each Critical/High
- final architecture diagram
- final code reading order
- final quickstart
- final demo script
- final test/eval report

Only after audit, implement Critical/High fixes as separate tasks.

Final statement must distinguish:
- production-oriented
- production-ready
Do not claim the latter unless evidence truly supports it.
