# Phase 5 Prompt — Skills, Tools, Context

Split this phase into small tasks after the interfaces are approved.

## Goal

Create explicit Skill/Tool/Context abstractions so TaskPilot is extensible without turning every capability into a new Agent.

## Part A — Skill registry

Define minimal manifest fields:
- id/name
- version
- purpose
- input contract
- output contract
- tool dependencies
- risk ceiling
- instructions/reference path
- eval cases

Implement registry and selection interface.

## Part B — Tools

Implement typed tools for V1:
- research/search adapter (mockable)
- document/knowledge retrieval
- safe tabular analysis
- one mock/internal action for later approval testing

Every tool:
- typed args/result
- risk level
- trace hooks
- normalized errors
- test doubles

## Part C — Context Builder

Build a `ContextEnvelope` from:
- task
- acceptance criteria
- current step
- selected conversation
- user memory
- org knowledge
- relevant tool history

Add provenance and budget report.

## Part D — three V1 skills

- research
- document_analysis
- tabular_analysis

## Tests

- correct skill lookup
- unavailable tool handling
- context source provenance
- tenant-filtered KB
- context budget trimming
- tool error normalization

Do not implement a skill marketplace.
