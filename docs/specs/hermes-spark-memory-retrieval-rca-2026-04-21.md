# Hermes Spark Memory Retrieval RCA

Date: 2026-04-21

This note records the investigation into why Hermes Spark answered that it did
not know Richard Jhang's food sensitivities even though the information exists
in MemOS.

## Summary

Hermes Spark's memory provider was loaded and configured, but the relevant
food-sensitivity memory was not injected into the model prompt on the failed
turn.

This is not a "memory absent" failure. A direct MemOS query against the correct
HTTPS base URL returned the expected memory from the Hermes Spark namespace.

The likely failure class is in Hermes Spark's retrieval path:

- pre-turn MemOS recall did not make it into the prompt
- or it returned empty/irrelevant results and failed silently
- while MemPalace fallback was also unhealthy or skipped

## Incident trigger

Observed bad answer:

- Hermes Spark responded that it had no information about Richard's personal
  food sensitivities and asked the user to share them.

The relevant session transcript is in:

- `~/.hermes/sessions/session_20260420_203438_3d77e8.json`

## Key findings

### 1. Hermes Spark did load the external memory provider

During the failing turn the process loaded `~/.hermes/.env` and registered the
`memos_palace` provider.

Observed in `~/.hermes/logs/agent.log`:

- `2026-04-20 20:54:04` inbound message asking about food sensitivity
- `2026-04-20 20:54:04` environment loaded from `~/.hermes/.env`
- `2026-04-20 20:54:05` memory provider `memos_palace` registered

This rules out the simplest configuration failure where the provider never
initialized.

### 2. The built-in markdown memory did not contain the fact

Local built-in memory files only contained:

- emoji rules
- location split between San Francisco and Honolulu
- naming preference for "Hermès"
- technical/tooling notes

No food-sensitivity information was present in:

- `~/.hermes/memories/MEMORY.md`
- `~/.hermes/memories/USER.md`

Therefore, if external recall returns empty, Hermes Spark will plausibly claim
it does not know.

### 3. Canonical MemOS identity was configured correctly

The custom provider config sets:

- `owner_user_id = rj@stratminds.vc`

in:

- `~/.hermes/memos_palace.json`

So this was not a Telegram-ID-to-email mapping bug on the MemOS side.

### 4. MemPalace was unhealthy around the same period

The log shows repeated MemPalace failures:

- `401 Unauthorized`
- `Server disconnected without sending a response`
- occasional connection-refused/store failures

This matters because MemPalace is the fallback verbatim/evidence layer in the
custom provider. Even if MemOS missed the fact, fallback recall was not
reliable.

### 4a. MemPalace was later repaired, but still did not return relevant recall

After the initial RCA, MemPalace was retested from the Hermes Spark runtime
using the provider's exact MCP flow:

- `initialize` with protocol version `2024-11-05`
- `tools/call` with `name = search_authorized`

That retest established a different current state from the earlier logs:

- authentication is now working
- MCP transport is working
- `search_authorized` executes successfully

However, relevant food-sensitivity recall still did not appear.

Observed live results:

- query `food sensitivities peanuts almonds dairy egg soy beef oysters`
  returned `0` authorized results
- query `food sensitivities` returned `0` authorized results
- query `diet allergies food` returned `0` authorized results
- query `peanuts almonds dairy egg soy beef oysters` returned `2` results,
  but both were irrelevant false matches from unrelated archived content in
  `agent_main/technical` and `agent_main/planning`

This changes the diagnosis for MemPalace specifically:

- the old `401 Unauthorized` problem appears fixed
- the remaining issue is no longer transport/authentication
- the remaining issue is data availability, scope, or search relevance

So MemPalace is no longer the operational blocker for this incident. It is
currently functioning, but it does not contain a useful archival hit for this
topic.

### 5. Direct MemOS retrieval succeeded once the correct endpoint was used

The originally configured/stated MemOS endpoint in older notes used plain HTTP
on port 8000:

- `http://openclaw-gcp.tailc13f7e.ts.net:8000`

That endpoint was not reachable during this investigation.

The user provided the corrected live endpoint:

- `https://openclaw-gcp.tailc13f7e.ts.net/memos`

Using that corrected base URL, a direct search succeeded:

- `GET /docs` returned `200`
- `POST /product/search` returned the expected memory

The returned memory text included:

- extreme sensitivity to peanuts and almonds
- high sensitivities to dairy, egg, soy, beef, and oysters

The returned hit was in:

- cube `platform:hermes/node:rj-spark`

This is critical because it proves the fact was:

- present in MemOS
- in Hermes Spark's own namespace
- retrievable for the canonical owner

So the failure was not caused by the fact living only in another agent's cube.

## What likely failed

## A. Hermes Spark did not surface the retrievable MemOS hit into prompt context

The provider code performs pre-turn recall in `plugins/memos_palace/__init__.py`
by calling `_build_recall_context()`.

The call path is:

- `run_agent.py` calls `prefetch_all()`
- `MemoryManager.prefetch_all()` merges provider recall text
- the result is only injected if non-empty

The provider's cold-start `prefetch()` waits only `0.75s` before giving up and
returning `""`.

If MemOS is slow, transiently unavailable, or throws any exception, the failure
degrades to empty recall with no user-visible warning.

That behavior is consistent with the observed answer: the model acted as though
only built-in memory was available.

## B. MemOS failures in prefetch are too silent

The provider wraps MemOS recall failures and degrades to empty results.

This makes the user-visible symptom ambiguous:

- genuine "no memory found"
- timeout
- schema mismatch
- network issue
- backend error

all collapse into the same model behavior.

## C. MemPalace fallback conditions are too narrow for this class of query

The provider only uses MemPalace fallback when:

- the query looks like verbatim/exact-recall/evidence intent
- or MemOS returns fewer than two results

`"what do you know about my food sensitivities?"` is a personal-memory query,
but it does not strongly match the current verbatim/evidence heuristics.

If MemOS returned irrelevant hits, MemPalace could be skipped even though the
question was clearly asking for stored personal knowledge.

## D. Endpoint drift may have contributed

Older integration notes and local assumptions referenced MemOS at:

- `http://openclaw-gcp.tailc13f7e.ts.net:8000`

The working endpoint during this investigation was:

- `https://openclaw-gcp.tailc13f7e.ts.net/memos`

If Hermes Spark's live runtime or related tooling still points at the older
endpoint anywhere in the actual retrieval path, that would directly explain why
manual retrieval can work while the agent still misses memories.

This requires verification in the live running process, but it is now an
important suspect.

## Direct evidence recovered from MemOS

A direct search at the corrected MemOS endpoint returned a memory record stating
that Richard Jhang has:

- extreme sensitivity to peanuts and almonds
- high sensitivities to dairy, egg, soy, beef, and oysters

The hit metadata also indicated:

- cube: `platform:hermes/node:rj-spark`
- source type: `openclaw_chat`
- horizon: `long_term`
- owner principal: `rj@stratminds.vc`
- confidence: `0.99`

That confirms the underlying semantic memory exists and is queryable.

## Conclusion

The failure was not caused by missing food-sensitivity data in MemOS.

The failure was caused by Hermes Spark not successfully surfacing a retrievable
MemOS memory into the model prompt on the relevant turn.

The highest-probability explanations are:

1. prefetch-time MemOS recall returned empty because of endpoint drift, timeout,
   or transient failure
2. that failure degraded silently to no injected context
3. MemPalace fallback did not rescue the turn

At the time of the original failure, MemPalace was degraded. In the current
state, MemPalace is operational again, but still does not return relevant food-
sensitivity recall, so MemOS remains the only confirmed source for that fact.

## Open questions

1. Does the live Hermes Spark runtime still use `MEMOS_API_URL` from `.env`, or
   is some path still pinned to the older `:8000` endpoint?
2. What is the actual latency of `POST /product/search` at the corrected HTTPS
   `/memos` endpoint relative to the provider's `0.75s` cold-start budget?
3. On the failed turn, did MemOS return empty, time out, or fail before
   returning the relevant memory?
4. Does the relevant conversation ever exist in MemPalace under the private
   `viewer = subject = rj@stratminds.vc` scope?
5. Should personal-memory questions force broader fallback semantics even if
   MemOS returns unrelated hits?

## Recommended next debugging steps

1. Log the effective `memos_api_url` used by the live provider at init time.
2. Log MemOS prefetch latency and whether prefetch returned empty, timed out, or
   errored.
3. Compare the live runtime's effective endpoint against the corrected HTTPS
   `/memos` base URL.
4. Confirm whether MemOS search at the corrected endpoint consistently completes
   within the current prefetch budget.
5. Audit whether the source conversation containing the food-sensitivity fact
   was ever archived into MemPalace under the expected private scope.

## Resolution

Date: 2026-04-24

The failure mode identified in "Key findings" as "returned empty/irrelevant
results and failed silently" was confirmed and root-caused. The bug was not in
the MemOS server, the endpoint selection, the proxy path, or the prefetch
budget. It was a response-schema mismatch in the provider's parser.

### Root cause

`MemosPalaceProvider._extract_memos_hits` walked the MemOS `/product/search`
response looking for each hit's rendered memory text under the keys `text` or
`content`. The current MemOS product API returns that text under the key
`memory` instead. Every hit matched neither branch, so the parser dropped it,
and `memos_search` returned `{"success": true, "results": []}` even when the
server had returned dozens of relevant hits.

The same parser read `tags` and `info` only from the top level of each hit,
but MemOS nests both under `metadata.*`. So even in the code paths where
the hit text happened to land under `text`/`content`, tier/memory-type
signals were invisible to `_rerank_memories`.

Direct verification against the live server — same endpoint, same proxy env,
same auth as the running gateway — returned 28 matching hits for the food-
sensitivity query. The same response fed through the pre-fix extractor yielded
zero hits; through the post-fix extractor it yielded the expected set with
tags and `info.tier` surfaced.

### Fix

In `plugins/memos_palace/__init__.py`, `_extract_memos_hits` now accepts
`text` or `content` or `memory` as the content key, and reads `tags` / `info`
from `metadata.*` as a fallback to the top level. One short comment in the
code records why all three keys are accepted (server schema drift that would
otherwise silently drop every hit).

### What this changes for the open questions

Question 3 — "did MemOS return empty, time out, or fail" — is resolved:
MemOS returned non-empty results, but the provider dropped them at parse
time. Questions 1, 2, and 4 remain as written; question 5 (should personal-
memory questions force broader fallback semantics) is no longer load-bearing
for this specific incident, since the primary path now succeeds.
