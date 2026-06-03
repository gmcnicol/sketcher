# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repo root if it exists - it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** - read system-wide ADRs that touch the area you're about to work in.
- **`webapp/CONTEXT.md`** and **`webapp/docs/adr/`** - read these for Node.js web app work.
- **`model-builder/CONTEXT.md`** and **`model-builder/docs/adr/`** - read these for Python/uv model-builder work.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

This repo is expected to use a multi-context layout:

```text
/
├── CONTEXT-MAP.md
├── docs/adr/                          # system-wide decisions
├── webapp/
│   ├── CONTEXT.md
│   └── docs/adr/                      # web app decisions
└── model-builder/
    ├── CONTEXT.md
    └── docs/adr/                      # model-builder decisions
```

## Contexts

- **webapp** - Node.js web app for reviewing generated sketch candidates, recording survive/die decisions, and eventually previewing sketched animation and color variants.
- **model-builder** - Python/uv model builder for sketch generation, mutation, survivor history, fitness heuristics, and export pipelines.

## Use the glossary's vocabulary

When your output names a domain concept in an issue title, refactor proposal, hypothesis, or test name, use the term as defined in the relevant `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal - either you're inventing language the project doesn't use, or there's a real gap. Note it for `/grill-with-docs`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) - but worth reopening because..._
