# Agency

Agent-driven, AI-supervised digital marketing agency, built as an orchestration
layer on top of a self-hosted [Postiz](https://github.com/gitroomhq/postiz-app)
instance.

## Architecture

- **Postiz is external.** This repo doesn't vendor or fork Postiz — it's a
  separate service (deployed however you like, e.g. rootless Podman/Quadlet)
  and this codebase only talks to its `/public/v1` REST API. Upgrading Postiz
  is just bumping a container tag, with no merge conflicts against upstream.
- **Org hierarchy: Agency → Customer → Brand → Project.** One YAML file per
  customer (`org/<customer-slug>.yaml`) holds that customer's brands, each
  with its own Postiz group/integration IDs, voice, and guidelines. A brand's
  `projects` are a sub-brand layer -- campaigns, themes, initiatives, series --
  that inherit the brand's voice but can layer on extra guidelines/banned
  topics and their own topic focus. Brand-level `postiz_group_id` maps onto
  Postiz's native "customer/group" concept (`GET /public/v1/groups`), so
  multi-client scoping isn't reinvented.
- **Inference is local-first.** `agency.inference.LocalFirstProvider` tries a
  local OpenAI-compatible endpoint (Ollama by default) and falls back to
  OpenRouter (or any other OpenAI-compatible API) only on a connection
  failure or timeout. Swapping providers is a `.env` change.
- **Orchestration is LangGraph, not hand-rolled control flow.** A single
  post's lifecycle (draft → supervise → revise-loop → escalate → publish) is
  a `StateGraph` (`agency/graph.py`), compiled with a checkpointer
  (SQLite in production, in-memory in tests). That buys two things a plain
  function chain doesn't: every run is keyed by a `thread_id` and durable
  across process restarts, and a stuck/rejected draft can call `interrupt()`
  to pause -- for hours or days -- and later be resumed from a completely
  separate CLI invocation via `Command(resume=...)`. This is deliberately
  the *only* place a framework was adopted: the org data model and the
  Postiz/ComfyUI/knowledge-scraping clients stay plain Python, since no
  framework does that part for you anyway.
- **ComfyUI is a plain HTTP client, no framework, no GPU-specific code.**
  `agency/comfyui/client.py` wraps the real queue API (`POST /prompt`,
  `GET /history/{id}`, `GET /view`), verified against
  comfyanonymous/ComfyUI's own source and its official
  `script_examples/basic_api_example.py`. Workflows are host-specific (they
  name your installed checkpoints/custom nodes), so none is shipped as
  ready-to-run -- see `workflows/README.md` for exporting your own. AMD
  ROCm/Vulkan is entirely a property of how you build/run your ComfyUI
  instance; this client neither knows nor cares.
- **Seven agent roles so far:**
  - `KnowledgeAgent` scrapes brand-approved URLs into a per-brand corpus of
    markdown pages + downloaded images under
    `knowledge/<customer-slug>/<brand-slug>/` (checks `robots.txt` before
    every fetch; caps images per page and image size).
  - `TopicAgent` proposes what to post about, grounded in that brand's
    knowledge base titles, aware of recently-used topics (tracked in
    `state/<customer>/<brand>/<project-or-_default>/topic_history.json`),
    and aware of a project's `topic_hint` (its campaign focus) when set.
  - `ContentAgent` drafts copy in the brand's voice, grounded in the corpus
    via `KnowledgeBase` (lexical keyword overlap for the MVP, no embeddings
    yet).
  - `SupervisorAgent` reviews every draft against the brand's
    guidelines/banned topics, with up to one revision round before the
    graph escalates instead of silently giving up.
  - `Artist` writes an image-generation prompt from a brief + the brand's
    voice, then renders it via ComfyUI. `style` selects a workflow file
    (structurally different graphs); `checkpoint` overrides just the model
    within one workflow. Not yet wired into the post graph itself -- run it
    standalone via `agency illustrate` for now (see below).
  - `VideoMaster` is the same pattern for video (`agency animate`), meant to
    hand its output to a future `StudioWorker` for shorts/reels assembly.
  - `Designer` is the "intermediate entity" between content agents and the
    render agents: `recommend_style()` picks a workflow/checkpoint/negative
    prompt for a brief (used by `illustrate` whenever `--style` is omitted),
    and `review_asset()` is a QC gate on the rendered output against brand
    voice/audience/personality. Visual review needs a vision-capable model
    configured (e.g. Ollama's `llava`) -- `complete_with_image()` on the
    provider sends the standard OpenAI vision content-parts format
    (`image_url` as a base64 data URI), which is real, not a stub, but
    untested against an actual vision model in this sandbox.
- **Escalation replaces "Director" as a role, not an LLM.** When the
  supervisor can't approve a draft (no revision offered, or revisions
  exhausted), the graph's `escalate` node interrupts and the run sits in
  `agency review` until a human calls `agency resume`. That routing logic is
  plain Python -- no LLM call needed to decide "ask a human."
- **Multi-brand/customer batch + scheduling.** `agency run-all` walks every
  `org/*.yaml` customer, every brand, every project (or the brand itself if
  it has none): re-ingest `knowledge_sources`, propose topics, run the graph
  per topic. `agency loop` repeats that on an interval; on a systemd-managed
  host, `deploy/systemd/agency-run-all.{service,timer}` does the same as a
  timer instead of a long-lived process.

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

Create a customer file from the template:

```bash
cp org/example_customer.yaml org/my_customer.yaml
```

Fill in each brand's `postiz_group_id` and `integration_ids` using:

```bash
curl -H "Authorization: $POSTIZ_API_KEY" "$POSTIZ_BASE_URL/public/v1/groups"
curl -H "Authorization: $POSTIZ_API_KEY" "$POSTIZ_BASE_URL/public/v1/integrations"
```

Also fill in `knowledge_sources` (URLs to re-scrape on every scheduled run)
and `posts_per_run`. Add a `projects:` entry per brand for any campaign/theme
that should get its own topic focus and rotation.

## Run the vertical slice

Build a brand's knowledge base first (repeatable -- re-ingesting a URL
replaces its old entry):

```bash
python -m agency.cli ingest --org org/my_customer.yaml --brand my-brand \
  --url https://mybrand.com/about \
  --url https://mybrand.com/products/flagship
```

This writes `knowledge/<customer-slug>/<brand-slug>/pages/*.md` (with
source-url/title/fetched-at front matter) and
`knowledge/<customer-slug>/<brand-slug>/assets/*/img-N.*`.

Then draft one post (add `--project <slug>` to draft under a specific
campaign/theme):

```bash
python -m agency.cli draft --org org/my_customer.yaml --brand my-brand \
  --topic "our new fall collection"
```

This grounds the draft in whatever knowledge base content matches the topic,
runs it through the supervisor (with up to one revision round), and either
creates a `draft` post in Postiz, or — if the supervisor never approves —
prints a thread id and exits with status 2 so you know to check `agency
review`. Add `--dry-run` to skip the Postiz call entirely, or `--publish now`
/ `--publish schedule` once you're ready to go live.

## Generating an image (Artist + ComfyUI)

Requires a running ComfyUI instance (`COMFYUI_BASE_URL`, default
`http://localhost:8188`) and your own exported workflow -- see
`workflows/README.md`; the shipped `workflows/image/default.json` is
ComfyUI's own documentation example and won't render on your instance
as-is.

```bash
python -m agency.cli illustrate --org org/my_customer.yaml --brand my-brand \
  --brief "a red bicycle in a sunlit garage" --seed 42
```

Omit `--style` and the `Designer` picks one from whatever workflows exist
under `workflows_dir`; pass `--style default` to force a specific one. After
generation the `Designer` reviews the output (needs a vision-capable model;
add `--no-review` to skip). Saves output(s) to
`assets/<customer>/<brand>/generated/images/`. Not yet wired into
`draft`/`run-all` -- attaching a generated image to a Postiz post needs
`POST /public/v1/upload` first, which isn't built yet.

## Generating a video (VideoMaster + ComfyUI)

Same idea, your own workflow under `VIDEO_WORKFLOWS_DIR` (default
`./workflows/video`; no default is shipped -- see `workflows/README.md`):

```bash
python -m agency.cli animate --org org/my_customer.yaml --brand my-brand \
  --brief "a bicycle racing down a hill, drone shot" --style default
```

Saves to `assets/<customer>/<brand>/generated/videos/`. Also not yet wired
into publishing, and meant to eventually feed a `StudioWorker` rather than
go straight to Postiz.

## Human review queue

Any draft the supervisor can't approve pauses instead of silently failing:

```bash
python -m agency.cli review                                  # list what's pending
python -m agency.cli resume --thread-id <id> --approve        # publish as-is
python -m agency.cli resume --thread-id <id> --approve --text "edited copy"
python -m agency.cli resume --thread-id <id> --reject --reason "off brand"
```

Because the graph state is checkpointed to SQLite, `resume` can run in a
completely separate process, days after `run-all` created the escalation.

## Running the whole agency (multiple customers/brands, on a schedule)

```bash
python -m agency.cli run-all              # one pass over every org/*.yaml customer
python -m agency.cli run-all --dry-run    # same, but never calls Postiz
```

`run-all` always creates `type: draft` posts in Postiz -- it has no
`--publish` flag, since an unattended job should never be the thing that
takes a post live.

For recurring runs, either:

```bash
python -m agency.cli loop --interval-seconds 21600   # in-process, sleeps between cycles
```

or, on a systemd-managed host, install the provided timer instead of
keeping a process running:

```bash
mkdir -p ~/agency && cp -r . ~/agency   # adjust to wherever you actually deploy this
mkdir -p ~/.config/systemd/user
cp deploy/systemd/agency-run-all.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agency-run-all.timer
```

The unit files assume the repo lives at `~/agency` with a `.venv` and `.env`
there; adjust `WorkingDirectory`/`ExecStart` in the `.service` file if yours
lives elsewhere. `OnCalendar` in the `.timer` defaults to 09:00 and 17:00
daily -- edit to taste.

## Tests

```bash
python -m pytest
```

Postiz and LLM calls are mocked (`respx`, fakes); LangGraph checkpointing
uses `MemorySaver` instead of the SQLite backend. No live Postiz instance or
model runner is required to run the suite.

## Roadmap (not yet built)

- Wiring `Artist` into the post graph and Postiz (needs a
  `PostizClient.upload_media()` using `POST /public/v1/upload`, then
  attaching the returned media to a post's `value[].image`).
- Creative layer still open: a music/SFX agent, `GhostWriter` (long-form
  content, can ask `Designer` for illustrations/covers/diagrams), and
  `StudioWorker` (assembling the asset bank into shorts/reels/video,
  reporting to `Designer`).
- `Secretary` (per customer: deadlines, platform compliance, paperwork,
  bookkeeping) and `DevOps` (backups, operational security, pipeline health)
  as plain Python graph nodes -- deliberately not LLM agents, per the
  orchestration-framework research that shaped this design.
- An `R&D` node that can suggest better open-source tooling -- real value
  here needs a search-capable backend, which isn't wired up yet.
- Feedback loop from Postiz analytics (`GET /public/v1/analytics/:integration`)
  back into the content agent's and topic agent's prompts.
- Embedding-based retrieval in `KnowledgeBase` instead of keyword overlap,
  once corpora get large.
- Per-brand/project run cadence (right now every customer in `org/` gets the
  same interval from `loop`/the systemd timer).
