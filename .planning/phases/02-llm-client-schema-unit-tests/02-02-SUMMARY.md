---
phase: "02"
plan: "02"
status: complete
---

## Plan 02-02: LLMProfile.from_env + config/llm_profiles.yaml

### What was built

**`scripts/llm_client.py` (appended ~88 LOC):**
- `LLMProfile.from_env(name=None, yaml_path=None)` classmethod — env > yaml > dataclass default three-tier priority
- `_load_profiles_yaml(path=None)` module function — defensive yaml loading (missing → {}, malformed → {})
- 7 env constants: `_ENV_PROFILE` / `_ENV_BASE_URL` / `_ENV_MODEL` / `_ENV_API_KEY_ENV` / `_ENV_TIMEOUT` / `_ENV_STRUCTURED_MODE` / `_ENV_CONCURRENCY`
- `_DEFAULT_YAML_PATH = Path("config/llm_profiles.yaml")`
- Pure append — zero modifications to Plan 02-01 surfaces

**`config/llm_profiles.yaml` (new, committed):**
- `qwen3-vllm-lan` profile (json_schema, concurrency 5)
- `deepseek-cloud` profile (json_object, concurrency 10)
- No secrets — `api_key_env` pointers only, `base_url` uses `10.0.x.x` placeholder

**`tests/test_llm_profile.py` (11 cases):**
- C-1..C-10: env-all-set, env-overrides-yaml, yaml-fallback, yaml+default-fallback, yaml-missing, malformed-yaml, real-file, timeout-invalid, concurrency-invalid, required-field-missing
- C-7: api_key not in repr() security test

### Deviations

None — pure append, all Plan 02-01 surfaces byte-level preserved.

### Requirements covered

- LLM-03: LLMProfile supports multiple profiles (qwen3-vllm-lan + deepseek-cloud)
- CFG-01: 7 LLM_* env vars wired (LLM_PROFILE/BASE_URL/MODEL/API_KEY_ENV/TIMEOUT/STRUCTURED_MODE/CONCURRENCY)
- CFG-02: config/llm_profiles.yaml committed with two profiles; env > yaml > default priority

### Test results

- 11 new tests, all passing
- 247 total tests (218 baseline + 18 Plan 02-01 + 11 Plan 02-02), 0 failures

### Phase 3 integration points

```python
profile = LLMProfile.from_env(name=args.llm_profile)
client = AsyncLLMClient(profile, http=shared_httpx_client)
```

### Phase 2 overall: 7/7 requirements delivered

LLM-01..05 + CFG-01/02 all verified via Nyquist observation matrix.
