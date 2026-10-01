# Phase 14 Product Design

## Product direction

Phase 14 Implementation 1 established the Product workspace and result
visualization experience. The revised direction for Implementation 2 is:

> **Conversational Agent + Task Memory Foundation**

This direction replaces the earlier **Conversational Agent Interface** framing.
The Agent becomes a natural-language entry point to structured TaskPilot
operations while the existing task and run records remain the source of truth.

## Implementation 2 — Conversational Agent + Task Memory Foundation

### 1. Natural-language task creation

Users can describe a remote-sensing analysis requirement in natural language.
For example:

> “帮我分析武汉东湖最近几年植被有没有变化。”

The Agent interprets the request and proposes structured task information:

- analysis area;
- analysis type;
- indicator;
- time range.

The Agent returns a **Task Proposal** for the user to review. The server
validates the structured proposal, and the user must confirm it before a Task
is created or execution begins.

The intended flow is:

```text
User Input
    ↓
Agent Understanding
    ↓
Task Proposal
    ↓
Server Validation
    ↓
User Confirmation
    ↓
Task Creation
    ↓
Run Execution
```

Natural-language understanding proposes intent and parameters; it does not
bypass server validation, authorization, lifecycle rules, or confirmation.

### 2. Task history query

The Agent can answer questions about the user's existing remote-sensing
analysis history, such as:

> “我之前做过哪些遥感分析？”

History queries read the existing structured application data, including:

- User;
- Task;
- TaskRun;
- Analysis Type;
- Execution Status.

The query remains tenant- and user-scoped through the existing authorization
boundaries. Implementation 2 does not introduce a separate memory database.

### 3. Historical result access

The Agent can locate a previous analysis result, for example:

> “打开之前武汉东湖那个分析。”

The lookup follows the existing persistence and artifact boundaries:

```text
Task lookup
    ↓
TaskRun lookup
    ↓
Artifact retrieval
    ↓
Existing result visualization
```

The result is opened from the existing artifact and visualization flow; the
Agent does not create a second result store.

## Explicit non-goals

Implementation 2 does **not** introduce:

- RAG;
- a vector database;
- a document knowledge base;
- long-term semantic memory.

The current requirement is task understanding and retrieval of structured
business data already owned by TaskPilot. It is not external knowledge
retrieval.

## Future extension

RAG and a vector database may be considered later for capabilities such as:

- a remote-sensing knowledge assistant;
- document analysis;
- policy or specification retrieval;
- historical report summarization.

Those capabilities remain outside the Implementation 2 contract and do not
change the current Runtime architecture.

## Scope boundary

This document is a product design baseline for Implementation 2. It does not
authorize source-code, test, database-schema, dependency, or Runtime
architecture changes by itself. Those changes require a later implementation
task with its own accepted contract.
