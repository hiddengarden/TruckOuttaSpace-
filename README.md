# Agency

Agent-driven, AI-supervised digital marketing agency, built as an orchestration
layer on top of a self-hosted [Postiz](https://github.com/gitroomhq/postiz-app)
instance.

## Architecture

- **Postiz is external.** This repo doesn't vendor or fork Postiz — it's a
  separate service (deployed however you like, e.g. rootless Podman/Quadlet)
  and this codebase only talks to its `/public/v1` REST API. Upgrading Postiz
  is just bumping a container tag, with no merge conflicts against upstream.
- **Brand profiles map onto Postiz's native "groups" (customers).** Postiz
  already supports multiple clients/customers per instance, each with their
  own scoped social channels (`GET /public/v1/groups`,
  `GET /public/v1/integrations?group=<id>`). A brand profile here
  (`brands/*.yaml`) just records which group + integration IDs it owns,
  instead of reinventing multi-tenancy.
- **Inference is local-first.** `agency.inference.LocalFirstProvider` tries a
  local OpenAI-compatible endpoint (Ollama by default) and falls back to
  OpenRouter (or any other OpenAI-compatible API) only on a connection
  failure or timeout. Swapping providers is a `.env` change.
- **Three agent roles for the vertical slice:**
  - `KnowledgeAgent` scrapes brand-approved URLs into a per-brand corpus of
    markdown pages + downloaded images under `knowledge/<brand-slug>/`
    (checks `robots.txt` before every fetch; caps images per page and image
    size). One corpus per brand, kept separate from the code.
  - `ContentAgent` drafts copy in the brand's voice, grounded in that
    corpus: it pulls the top keyword-matching pages via `KnowledgeBase`
    (lexical overlap for the MVP, no embeddings yet) and includes them as
    context so it isn't inventing facts about the brand.
  - `SupervisorAgent` reviews every draft against the brand's
    guidelines/banned topics before anything is sent to Postiz.
  - Posts are created with `type: draft` by default, so Postiz's own UI
    remains the last human checkpoint before anything actually goes live on
    a social platform.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env`:
- `POSTIZ_BASE_URL` / `POSTIZ_API_KEY` — from your running Postiz instance
  (Settings -> Public API in the dashboard to generate a key).
- `OLLAMA_BASE_URL` / `OLLAMA_MODEL` — your local model runner.
- `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` — fallback if the local endpoint
  is unreachable.

Create a brand profile from the template:

```bash
cp brands/example_brand.yaml brands/my_brand.yaml
```

Fill in `postiz_group_id` and `integration_ids` using:

```bash
curl -H "Authorization: $POSTIZ_API_KEY" "$POSTIZ_BASE_URL/public/v1/groups"
curl -H "Authorization: $POSTIZ_API_KEY" "$POSTIZ_BASE_URL/public/v1/integrations"
```

## Run the vertical slice

Build the brand's knowledge base first (repeatable -- re-ingesting a URL
replaces its old entry):

```bash
python -m agency.cli ingest --brand brands/my_brand.yaml \
  --url https://mybrand.com/about \
  --url https://mybrand.com/products/flagship
```

This writes `knowledge/<brand-slug>/pages/*.md` (with source-url/title/
fetched-at front matter) and `knowledge/<brand-slug>/assets/*/img-N.*`.

Then draft:

```bash
python -m agency.cli draft --brand brands/my_brand.yaml --topic "our new fall collection"
```

This grounds the draft in whatever knowledge base content matches the topic,
runs the draft through the supervisor (with up to one revision round), and —
only if approved — creates a `draft` post in Postiz via the API. Add
`--dry-run` to skip the Postiz call entirely, or `--publish now` /
`--publish schedule` once you're ready to go live.

## Tests

```bash
python -m pytest
```

Postiz and LLM calls are mocked (`respx`, fakes) — no live Postiz instance or
model runner is required to run the suite.

## Roadmap (not yet built)

- Multiple brands run in a batch/cron loop instead of one CLI invocation.
- A scheduling/strategist agent that proposes topics instead of taking one
  via `--topic`.
- Feedback loop from Postiz analytics (`GET /public/v1/analytics/:integration`)
  back into the content agent's prompt.
- Embedding-based retrieval in `KnowledgeBase` (e.g. via Ollama's embeddings
  endpoint) instead of keyword overlap, once corpora get large.
- Scheduled/recurring re-ingestion so a brand's knowledge base stays current
  instead of only updating on manual `ingest` calls.
