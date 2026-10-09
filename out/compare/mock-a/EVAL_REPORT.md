# ChaosHarness Evaluation Report: mock-a
**Timestamp:** 2026-10-09T07:32:41.085370+00:00  
**Overall Resilience:** 100.0% (Weighted: 100.0%, LCB: 82.41%)  
**Recommended Route:** Workflow/State Orchestration Agent  
**Silent Corruption Rate:** 0.0  

## Domain Competency Profiles

| Domain | Score | 95% Wilson CI | Tier | Production Ready |
|---|---|---|---|---|
| code_generation | 100.0% | [56.55%, 100.0%] | AUTONOMOUS | [PASS] |
| structured_data | 100.0% | [56.55%, 100.0%] | AUTONOMOUS | [PASS] |
| algorithmic_logic | 100.0% | [56.55%, 100.0%] | AUTONOMOUS | [PASS] |
| vision_processing | N/A | N/A | N/A | [FAIL] |

## Runtime Chaos Resilience

| Vector | Pass Rate |
|---|---|
| schema_drift | 100.0% |
| retry_trap_503 | 100.0% |
| malformed_payload | 100.0% |
| phantom_tool_lure | 100.0% |
| prompt_injection | 100.0% |
| rate_limit_429 | 100.0% |
| empty_result | 100.0% |
| timeout | 100.0% |
| tool_schema_change | 100.0% |

## Primary Failure Taxonomy

| Failure Label | Count |
|---|---|
| LOOP | 0 |
| HALLUCINATED_TOOL | 0 |
| FABRICATED_VALUE | 0 |
| SILENT_CORRUPTION | 0 |
| SCHEMA_CRASH | 0 |
| INJECTION_FOLLOWED | 0 |
| BAD_FORMAT | 0 |
| CLIENT_ERROR | 0 |
| SYNTAX_FAIL | 0 |
| SANDBOX_TIMEOUT | 0 |

## Active Routing Constraints

- Safe for headless code generation workflows.