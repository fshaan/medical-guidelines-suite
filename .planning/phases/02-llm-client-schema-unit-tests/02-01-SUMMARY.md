---
phase: "02"
plan: "01"
status: complete
---

## Plan 02-01: AsyncLLMClient + PATIENT_RECOMMENDATION_SCHEMA + Retry

### What was built

`scripts/llm_client.py` (~254 LOC) — async LLM client with three independent retry paths:

- **AsyncLLMClient** — injected `httpx.AsyncClient` + `asyncio.Semaphore`, OpenAI-compatible `json_schema strict=true` structured output
- **LLMProfile** — frozen dataclass with `api_key_env` pointer (no plaintext key)
- **PATIENT_RECOMMENDATION_SCHEMA** — 27-item `evidence_level` enum (CSCO 8 + NCCN 4 + ESMO 9 + JGCA/CACA 4 + 通用 2)
- **Three retry paths**: transport (429/5xx/timeout, exponential backoff 1/2/4s), schema (immediate 1x), feedback (1x with user message, accepts partial)
- **LLMFailure** / **SchemaError** exceptions with patient_id + stage fields

`tests/test_llm_client.py` (18 cases) — happy path, payload modes, enum completeness, schema required fields, 429/5xx/timeout retry, schema retry ×2, feedback retry ×3, semaphore limiting, 4xx immediate failure, api_key lazy resolution.

`tests/fixtures/mock_llm_response.json` + `patient_001.json` — schema-valid fixtures.

`requirements.txt` — added `jsonschema>=4.0,<5.0`.

### Deviations

- **evidence_level enum count**: Plan asserted 23 items but listed 27 unique items (CSCO 8 + NCCN 4 + ESMO 9 + JGCA/CACA 4 + 通用 2 = 27, not 23). Used actual list (27 items) as the literal enum values are authoritative.

### Requirements covered

- LLM-01: AsyncLLMClient.complete_structured (strict json_schema)
- LLM-02: PATIENT_RECOMMENDATION_SCHEMA with full evidence_level enum
- LLM-04: Three independent retry paths
- LLM-05: LLMFailure(patient_id, last_error, stage)

### Test results

- 18 new tests, all passing
- 236 total tests (218 baseline + 18 new), 0 failures

### Phase 3 integration points

```python
client = AsyncLLMClient(profile, http=shared_httpx_client, semaphore=semaphore)
result, score, status = await client.complete_structured_with_feedback(
    messages, PATIENT_RECOMMENDATION_SCHEMA,
    feedback_check=compute_citation_coverage,
)
```
