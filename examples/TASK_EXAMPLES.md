# Example Tasks for Development/Evaluation

## Research

Objective:
Research a specified technical topic using allowed sources and internal KB.

Acceptance:
- answer 5 defined questions
- distinguish source evidence from inference
- include source references
- flag unknowns

## Document Comparison

Objective:
Compare two uploaded project proposals.

Acceptance:
- extract budget
- timeline
- owner
- risks
- no invented missing values

## Tabular Analysis

Objective:
Find regions whose metric declined > 30%.

Acceptance:
- deterministic calculation
- list rows/regions
- include calculation basis
- verifier checks threshold logic

## Approval

Objective:
Prepare an outbound action to a mock external system.

Acceptance:
- action does not execute before approval
- unauthorized user cannot approve
- approved action executes once
- rejected action never executes

## Recovery

Objective:
Call a mock tool configured to fail on first invocation.

Acceptance:
- first failure traced
- retry budget respected
- second invocation succeeds
- final task succeeds
