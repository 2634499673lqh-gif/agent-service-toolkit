# Phase 8 Prompt — Evaluation

## Goal

Create a repeatable evaluation system so changes can be measured.

## Dataset

Version a small suite with:
- research cases
- document cases
- tabular deterministic cases
- retry/recovery cases
- approval/safety cases
- tenant isolation cases

## Metrics

At least:
- task completion
- plan schema validity
- expected tool/skill choice
- deterministic answer accuracy where applicable
- verifier correctness
- retry/recovery success
- approval policy correctness
- latency
- token/cost where available

## Runner

- reproducible command
- machine-readable output
- human summary
- model/version/config recorded
- compare against prior run when available

Do not use the same LLM's self-rating as the only correctness signal.

Add a small CI smoke gate that does not require expensive live LLM calls.
