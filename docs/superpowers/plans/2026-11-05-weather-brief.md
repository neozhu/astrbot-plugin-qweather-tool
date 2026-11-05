# Weather Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fetch richer weather facts while guiding concise, plain Chinese answers without icons or emoji.

**Architecture:** Preserve the primary weather result. Add a weather_brief module for supplemental aggregation, independent failure handling, bounded parallel requests, cache and single-flight reuse. The host LLM still writes the answer.

**Tech Stack:** Python 3.12, httpx, asyncio, pytest, AstrBot 4.x.

**Spec:** docs/superpowers/specs/2026-11-05-weather-brief-design.md

## Global Constraints

- One public get_weather(location, when, focus); three existing configuration fields.
- Default one or two paragraphs, plain text, no icons or emoji; no extra LLM or direct send.
- Never substitute current data for future forecasts or invent unavailable indices/probabilities.
- Keep HTTPS verification, bounded caching, safe error codes, and no new dependencies.

## Review Focus

- Tomorrow or unsupported dates: no current conditions or today's indices attributed to that date.
- Missing or malformed supplemental data: primary query remains usable, no false all-clear.
- Partial hourly coverage: report bounds and individual-hour probabilities, never entire-day certainty.
- Concurrent repeated calls: reuse cache and single-flight without multiplying upstream requests.
- Provider exceptions: never serialize request URL or key into brief warnings.

### Task 1: Supplemental API contracts and weather fields

Files: qweather_client.py, weather_models.py, tests/test_weather_brief.py, tests/conftest.py.
Interfaces: get_indices(location_id), get_air_quality(lat, lon), get_warnings(location_id) return dict or None; normalized current preserves direction/gust/humidity.
- [x] Write MockTransport tests for endpoint paths, params, auth, malformed bodies, denied auth and legitimate empty warnings; run and confirm failures.
- [x] Implement bounded optional HTTP methods returning None on failures, without logging raw exception strings.
- [x] Preserve current wind direction and enrich source fields; test normalization against literal examples.
- [x] Run targeted tests and inspect diff.

### Task 2: Brief assembly and service integration

Files: weather_brief.py (new), weather_service.py, tests/test_weather_brief.py.
Interfaces: WeatherBriefBuilder(client, cache, single_flight).build(loc, target, now) -> dict. Consumes Task 1 APIs and existing normalized forecasts.
- [x] Write integration tests with fixed clock and HTTP transport for complete brief, later rain, missing values, future dates, failure isolation, and concurrent cache reuse; confirm failures.
- [x] Implement parallel cached supplementary collection, matching dates, timezone-aware hour filtering and min/max/peak summaries; integrate only after successful normal weather query, outside comparison/historical branches.
- [x] Bound total enrichment time; provide per-source warning codes and coverage timestamps; retain original primary status.
- [x] Run full pytest suite; ensure offline tests never depend on external networking.

### Task 3: Reply guidance and user documentation

Files: main.py, README.md, tests/test_weather_brief.py, docs/AstrBot-Weather-Tool-SPEC-v1.3.md.
Interfaces: response_guidance in tool result for every platform, retaining WeChat formatting rules; no signature changes.
- [x] Test returned payload contains brief facts and response guidance for WeChat and other platforms, including missing-data paths; confirm new assertions fail.
- [x] Add shared concise-brief guidance to tool results and tool description; preserve existing WeChat prompt hooks.
- [x] Document behavior, optional API coverage/permissions, cost/caching, and superseding SPEC extension.
- [x] Run full pytest, node tests/test_plugin_view.mjs, pip check, and diff check; review whole change before reporting.

## Execution ledger

User approved the design in conversation; proceed inline without another approval round. Existing cloud checkout is isolated and already on branch work, so no worktree is needed. Leave changes reviewable in the working tree; no push, merge, or publication. Standalone skill helper scripts are not mounted; record progress here.


## Verification and review record

- Task 1 complete: 8 new API/normalization tests first failed on missing methods/fields, then passed.
- Task 2 complete: 6 integration tests first failed on absent brief; all passed after implementation. Tests additionally cover date filtering, UTC instants and half-open hour bounds, missing amounts and later rain.
- Task 3 complete: daily wind and cross-platform response-guidance assertions first failed, then passed. Existing tests were updated only for the approved new contracts (today may supplement current in a separate brief, peak probability replaces first-hour probability, tool results carry response guidance).
- Ruling: keep changes in the existing isolated cloud checkout and working tree without commits/push/merge; the user requested implementation, not publication.
- Ruling: block default external httpx transports in offline tests while preserving explicit MockTransport. This prevents pre-existing partially mocked tests from using real network calls and quota.
- Independent whole-change review by review_weather_brief identified an important per-caller timeout/shared-cancellation issue. Owner/follower cancellation and joining a slow primary all failed before the fix, then passed. Shared work now lives in a shielded independent task and every supplemental caller has its own deadline.
- Ruling: promote malformed AQ/alert records from review polish to a validity requirement: invalid data must not cause health advice or an all-clear. Four regression cases failed, then passed after validating numeric AQI plus standard code and requiring usable warning identity/content.
- No deferred code-review findings. Live provider schema/entitlement and final host-LLM wording are unverified, not claimed as tested. Official documentation access returned HTTP 403; no live credentials were available.
- Final verification: `.venv/bin/python -m pytest -q` -> 104 passed, 2 pre-existing deprecation warnings; `node tests/test_plugin_view.mjs` -> 5 passed; `.venv/bin/python -m pip check` -> no broken requirements; `git diff --check` -> clean.
- README now includes the full eight-endpoint API inventory, index type mapping, new endpoints, call conditions and live-validation limitations requested by the user.
- Release follow-up: user explicitly requested a version bump and merge to main. Supersedes the earlier keep-uncommitted ruling: bump metadata, registration, status response and HTTP User-Agent from 0.1.0 to 0.1.1; commit the reviewed change, merge into the fetched main branch and synchronize origin/main after verification.
