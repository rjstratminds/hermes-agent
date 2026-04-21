# Hermes Runtime Variants

This repo tracks runtime-specific Hermes handoff artifacts as separate
variation repositories when the variant carries files that should evolve
independently from core `hermes-agent`.

## Tracked Variants

| Variant | Path | Tracking | Purpose |
| --- | --- | --- | --- |
| Hermes M3 | `hermes_m3/` | Git submodule | M3-specific MCP auth runbooks and helper scripts |

## Hermes M3

`hermes_m3/` is pinned as a submodule at:

```text
https://github.com/rjstratminds/hermes_m3.git
```

Use it for M3-only operational artifacts, such as Grain refresh helpers and
MCP auth migration notes. Do not copy M3-specific secrets into this repo or the
submodule.

## Hermes Spark

No `hermes_spark` checkout was present when this note was added. If Spark has
variant-specific files, track them the same way:

```bash
git submodule add https://github.com/rjstratminds/hermes_spark.git hermes_spark
```

If Spark only differs by runtime environment values or secrets, document the
non-secret differences instead of committing generated env files.

## Rules

- Track reusable docs and helper scripts.
- Do not track generated caches such as `__pycache__/` or `*.pyc`.
- Do not track live `.env` files, OneCli proxy URLs, bearer tokens, provider
  keys, or Telegram tokens.
- Prefer submodules for independent variant repos; prefer docs for small
  non-secret configuration differences.
