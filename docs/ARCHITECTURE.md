# Architecture

## Logical architecture

```mermaid
flowchart TD
    U[User / UI] --> API[FastAPI API]
    API --> AUTH[Auth + RBAC + Tenant Policy]
    AUTH --> TS[Task Service]
    TS --> DB[(PostgreSQL)]
    TS --> AR[Agent Runtime / LangGraph]

    AR --> CX[Context Builder]
    CX --> MEM[User Memory]
    CX --> KB[Org Knowledge]
    CX --> DB

    AR --> PL[Planner]
    PL --> EX[Executor]
    EX --> SR[Skill Registry]
    SR --> TOOLS[Tool Layer]
    TOOLS --> EXT[Files / Search / Data / External APIs]

    EX --> VE[Verifier]
    VE -->|pass| OUT[Final Result]
    VE -->|recoverable| RC[Recovery / Replan]
    RC --> EX

    EX --> RP[Risk Policy]
    RP -->|L2+| AP[Approval]
    AP -->|approved| EX
    AP -->|rejected| VE

    API --> OBS[Observability]
    AR --> OBS
    TOOLS --> OBS
    AP --> AUDIT[Audit]
```

## Boundary principles

- API owns transport concerns.
- Auth/RBAC owns identity and authorization.
- Task service owns business task lifecycle.
- Agent runtime owns execution state machine, not user authorization.
- Skill registry owns reusable capability metadata.
- Tool layer owns deterministic external actions.
- Context builder owns what model calls receive.
- Approval service owns human decision persistence and authorization.
- Observability owns trace/metrics, not business decisions.

## Deployment, V1

```mermaid
flowchart LR
    UI[Streamlit / later Next.js] --> API[FastAPI]
    API --> PG[(PostgreSQL)]
    API --> LLM[LLM Provider]
    API --> OBJ[Local/S3-compatible storage]
    API --> TRACE[Trace backend optional]
    API --> REDIS[(Redis optional)]
    REDIS --> WORKER[Worker optional]
```

Do not introduce worker/Redis until long-running concurrency behavior justifies it.

## Architecture invariants

1. Every persisted business resource belongs to a user or organization scope.
2. Every externally visible side effect has an idempotency mechanism.
3. Approval cannot be bypassed by calling a tool directly through an API route.
4. Agent state can be reconstructed enough to explain a run.
5. Verifier outputs structured reasons/evidence.
6. Trace is diagnostic; business truth lives in domain tables/state.
