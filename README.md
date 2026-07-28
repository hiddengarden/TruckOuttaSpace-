# Agency

Agent-driven, AI-supervised digital marketing agency, built as an orchestration
layer on top of a self-hosted [Postiz](https://github.com/gitroomhq/postiz-app)
instance.

## Architecture

- **Postiz is external.** This repo doesn't vendor or fork Postiz — it's a
  separate service (deployed however you like, e.g. rootless Podman/Quadlet)
  and this codebase only talks to its `/public/v1` REST API. Upgrading Postiz
  is just bumping a container tag, with no merge conflicts against upstream.
- **WordPress is the website-publishing fallback, not Elxis.** Both were
  candidates; Elxis's documented "REST API" turned out to be a content
  *source* for its microblog module (pulling external feeds in), with no
  found documentation of an endpoint for creating articles programmatically.
  WordPress's REST API is core, stable since 4.7, with Application Passwords
  (core since 5.6) as the standard non-deprecated auth for external apps --
  the safer operational bet. `agency/wordpress/client.py` wraps it
  (`wp-json/wp/v2`); per-brand config is optional (`wordpress:` in a
  customer's YAML), and the secret lives in `.env`, never in the YAML.
- **Org hierarchy: Agency → Customer → Brand → Project.** One YAML file per
  customer (`org/<customer-slug>.yaml`) holds that customer's brands, each
  with its own Postiz group/integration IDs, voice, and guidelines. A brand's
  `projects` are a sub-brand layer -- campaigns, themes, initiatives, series --
  that inherit the brand's voice but can layer on extra guidelines/banned
  topics and their own topic focus. Brand-level `postiz_group_id` maps onto
  Postiz's native "customer/group" concept (`GET /public/v1/groups`), so
  multi-client scoping isn't reinvented.
- **Inference is local-first, and local-only by default.**
  `agency.inference.LocalFirstProvider` tries a local OpenAI-compatible
  endpoint (Ollama by default) and only ever calls OpenRouter (or any other
  OpenAI-compatible API) if that brand's `allow_cloud_fallback: true` is set
  in its YAML -- the default is `false`, so a brand with nothing configured
  never leaves the machine. When fallback isn't allowed and the local
  endpoint is unreachable, the call raises `LocalInferenceUnavailable`
  instead of silently going to the cloud; `run-all` catches it per
  brand/project (records a `local_inference_unavailable` event to the run
  ledger and skips just that unit), and single-shot commands like `draft`
  print guidance and exit non-zero.
- **Orchestration is LangGraph, not hand-rolled control flow, split into a
  compose graph and a finalize graph.** `agency/graph.py`'s
  `build_compose_graph` (draft → supervise → revise-loop → escalate) has no
  ComfyUI or Postiz dependency at all -- it only ever touches the local LLM.
  `build_finalize_graph` (illustrate → publish) picks up from whatever a
  compose run already checkpointed under the same `thread_id` (state
  persists across differently-shaped compiled graphs sharing a checkpointer,
  verified empirically) and is the only place ComfyUI/Postiz calls happen.
  Both are compiled with a checkpointer (SQLite in production, in-memory in
  tests), which buys two things a plain function chain doesn't: every run is
  durable across process restarts, and a stuck/rejected draft can call
  `interrupt()` to pause -- for hours or days -- and later be resumed from a
  completely separate CLI invocation via `Command(resume=...)`. The finalize
  graph also carries an explicit idempotency guard (`route_before_illustrate`,
  checked as the first thing on any invoke, before a retry can waste a
  ComfyUI render): if a prior attempt marked `publish_attempted` but never
  recorded a `postiz_response`, that's treated as ambiguous (process may have
  crashed mid-`create_post`) and the retry skips publishing rather than
  risking a duplicate -- `run.py:run_finalize` checks the same state before
  ever building fresh input, since an explicit `invoke()` input always wins
  over checkpoint values for the same keys (also verified empirically, and
  the reason `initial_finalize_input()` never sets `image_media`/
  `postiz_response`/`publish_attempted` itself). This framework split is
  deliberately the *only* place a framework was adopted: the org data model
  and the Postiz/ComfyUI/knowledge-scraping clients stay plain Python, since
  no framework does that part for you anyway.
- **ComfyUI is a plain HTTP client, no framework, no GPU-specific code.**
  `agency/comfyui/client.py` wraps the real queue API (`POST /prompt`,
  `GET /history/{id}`, `GET /view`), verified against
  comfyanonymous/ComfyUI's own source and its official
  `script_examples/basic_api_example.py`. Workflows are host-specific (they
  name your installed checkpoints/custom nodes), so none is shipped as
  ready-to-run -- see `workflows/README.md` for exporting your own. AMD
  ROCm/Vulkan is entirely a property of how you build/run your ComfyUI
  instance; this client neither knows nor cares.
- **Org-layer roles are deliberately mostly plain Python, not LLM agents**
  -- per the adopted framework research ("13 roles != 13 LLM agents";
  deterministic work doesn't need a model call):
  - `Secretary` (one per customer): `check_deadlines()` reads each brand's
    `Project.due_date`; `check_platform_compliance()` flags disabled Postiz
    integrations via the same `GET /public/v1/integrations` used elsewhere.
    Paperwork/bookkeeping/security-auth admin have no concrete system here
    to integrate with (none was specified), so `extra_checks` is a
    bring-your-own extension point instead of a fabricated integration.
  - `DevOps`: `check_service_health()` (Postiz/Ollama/ComfyUI reachability),
    `check_env_file_permissions()`, `backup()` (real `tarfile` archives of
    org/knowledge/state/assets/content -- verified with an actual archive
    in this sandbox, not just mocked), and `consult_rnd()`.
  - `RnDAgent` (one for the whole system): suggests open-source tooling
    improvements from the model's own knowledge -- explicitly no live web
    search wired in, so it can be stale about current tooling; that's a
    documented limitation, not a silent gap.
- **Ten agent roles in the creative/content layer:**
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
    within one workflow. Runs standalone via `agency illustrate`, or inside
    the finalize graph's `illustrate` node when `--with-image`/
    `--with-images` is passed to `draft`/`resume`/`run-all`/`loop` -- built
    fresh per brand/topic in `run_all` rather than shared, so its own
    prompt-writing call respects that brand's `allow_cloud_fallback`.
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
  - `GhostWriter` writes long-form publications (books/courses/ebooks)
    chapter by chapter, and can ask `Designer` + `Artist` for a cover and
    (optionally) per-chapter illustrations. `agency write-publication`
    saves the result as a single markdown file under
    `content/<customer>/<brand>/`.
  - `MusicAgent` writes music/SFX prompts and renders via ComfyUI's native
    audio nodes (output collected under the `"audio"` key -- verified
    against `SaveAudio`'s source, a different key from images/video). Falls
    back to a bring-your-own `MusicApiProvider` if no local audio workflow
    is configured, since no cross-vendor standard exists there.
  - `StudioWorker` assembles the brand's existing asset bank (images/audio,
    or raw video clips) into shorts/reels via `ffmpeg` -- it generates
    nothing itself, only edits what Artist/VideoMaster/MusicAgent already
    produced. Reports to `Designer`: `assemble_and_review()` extracts the
    result's first frame and runs it through the same QC pass used
    elsewhere. `ffmpeg` itself isn't installable in this sandbox, so this
    is verified against a mocked runner (exact command/concat-file syntax
    asserted) rather than a real render.
- **Escalation replaces "Director" as a role, not an LLM.** When the
  supervisor can't approve a draft (no revision offered, or revisions
  exhausted), the graph's `escalate` node interrupts and the run sits in
  `agency review` until a human calls `agency resume`. That routing logic is
  plain Python -- no LLM call needed to decide "ask a human."
- **Multi-brand/customer batch, in two global phases, with per-brand/project
  on-off control.** `agency run-all` walks every `org/*.yaml` customer, every
  *active* brand (`active: true` is the default; set `false` in a brand's or
  project's YAML to pause it without deleting config, e.g. to hand-tune how
  much of a scheduled run's time budget goes where), every active project
  (or the brand itself if it has none): re-ingest `knowledge_sources`,
  propose topics, run the compose graph per topic. Phase 1 does this for
  *every* active brand/project across *every* customer before Phase 2 runs
  any finalize (illustrate+publish) work -- not per-brand -- because Ollama
  and ComfyUI share one GPU, and interleaving them per-brand would still
  contend across brand boundaries; only a whole-run-wide split avoids it.
  Between phases, `unload_ollama()` frees VRAM (`POST /api/generate` with
  `keep_alive: 0`, per Ollama's own FAQ) before any ComfyUI call. `--only
  customer-slug/brand-slug` (repeatable) restricts a one-off run to specific
  brands without touching `active` in the YAML. `agency loop` repeats
  `run-all` on an interval; on a systemd-managed host,
  `deploy/systemd/agency-run-all.{service,timer}` does the same as a timer
  instead of a long-lived process.
- **Every run is diagnosable after the fact.** `agency.ledger.RunLedger`
  appends one JSON line per notable event (`compose_done`,
  `local_inference_unavailable`, `finalize_done`,
  `finalize_ambiguous_skip`, cloud-fallback events) to
  `state/run_ledger.jsonl` -- since `run-all`/`loop` are meant to run
  unattended on a schedule, this is the record of what actually happened
  without needing to have been watching the terminal. Shared JSON state
  files that multiple invocations could touch concurrently (e.g. topic
  history) are guarded with `agency.filelock.locked()`, a thin
  `fcntl.flock` wrapper, so an overlapping `run-all` and a manual `draft`
  can't race on a read-modify-write.

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

Two more per-brand (and, for `active`, also per-project) fields, both
optional and defaulted safely:
- `allow_cloud_fallback: true` -- opt this brand into OpenRouter when the
  local model is unreachable. Omit it (or set `false`) to keep the brand
  local-only, which is the default for every brand.
- `active: false` -- pause a brand or project so `run-all`/`loop` skip it
  entirely, without deleting its config. Defaults to `true`.

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

Add `--with-image` to also generate an image (via `Artist`, default ComfyUI
workflow style, using the topic itself as the image brief) and attach it to
the post -- uploaded through `PostizClient.upload_media()`
(`POST /public/v1/upload`) before the post is created, so it's a real
`MediaDto` reference, not a local path. `run-all`/`loop` take the equivalent
`--with-images` flag.

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

## Writing a long-form publication (GhostWriter)

```bash
python -m agency.cli write-publication --org org/my_customer.yaml --brand my-brand \
  --brief "a beginner's guide to widgets" --chapters 5
```

Add `--illustrate-chapters` to get an image per chapter (not just a cover),
or `--no-illustrations` to skip ComfyUI entirely and get text only. Saves to
`content/<customer>/<brand>/<slug>.md`.

Add `--publish-to-wordpress draft` (or `publish`/`pending`/`future`) to also
post it to the brand's WordPress site. Requires a `wordpress:` block on that
brand in its `org/*.yaml`:

```yaml
wordpress:
  base_url: "https://my-brand.example.com"
  username: "agency-bot"
  app_password_env: "WORDPRESS_APP_PASSWORD_MY_BRAND"   # set in .env, not here
```

Generate the Application Password under WordPress admin -> Users -> Profile
-> Application Passwords. The cover and every chapter illustration are
uploaded as WordPress media first, with their markdown links rewritten to
the uploaded URLs, so images actually resolve on the live post.

## Generating music/SFX (MusicAgent + ComfyUI)

Your own workflow under `AUDIO_WORKFLOWS_DIR` (default `./workflows/audio`;
no default is shipped -- see `workflows/README.md`):

```bash
python -m agency.cli compose --org org/my_customer.yaml --brand my-brand \
  --brief "upbeat synth pop background track, 120bpm"
```

Saves to `assets/<customer>/<brand>/generated/audio/`.

## Assembling a short/reel (StudioWorker)

Requires `ffmpeg` on your `PATH` (not this repo's concern to install). By
default pulls every image from the brand's own asset bank
(`assets/<customer>/<brand>/generated/images/`):

```bash
python -m agency.cli assemble --org org/my_customer.yaml --brand my-brand \
  --brief "product highlights reel" --audio assets/my-customer/my-brand/generated/audio/track.flac
```

Or pass explicit `--image` paths. Saves to
`assets/<customer>/<brand>/generated/videos/assembled.mp4`, and (unless
`--no-review`) has `Designer` review the first extracted frame.

## Admin, DevOps, and R&D

```bash
python -m agency.cli admin-report --org org/my_customer.yaml   # Secretary
python -m agency.cli devops-health                              # Postiz/Ollama/ComfyUI + .env perms
python -m agency.cli devops-backup --backup-dir ./backups        # tar.gz org/knowledge/state/assets/content
python -m agency.cli devops-rnd --focus "video pipeline"         # DevOps consults RnDAgent
```

`admin-report`'s Postiz-reachability check and `devops-health`'s findings
both open an email ticket (see Notifications below) for anything at
`warning`/`overdue`/`down` severity, instead of only ever printing to a
terminal nobody may be watching. A Postiz outage during `admin-report`
surfaces as a `warning`-severity finding rather than crashing the report.

```bash
python -m agency.cli preflight
```

Sanity-checks the whole stack before you trust a scheduled run: Postiz/
Ollama/ComfyUI reachability, `.env` permissions, `POSTIZ_API_KEY` presence,
whether Telegram/SMTP are configured, every ComfyUI workflow's
`.mapping.json` actually resolving against its `.json` template (catches a
typo'd node id or renamed input before a real generation hits it), and that
every file under `org/` parses. Exits non-zero if anything's wrong, so it
can gate a deploy.

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

Both commands verify against the checkpoint, not just the escalations JSON
file: `EscalationRegistry.list_verified()`/`get_verified()` build a
throwaway compose graph bound to the same checkpointer and check
`get_state().next` -- an entry only counts as pending if its thread is
still genuinely parked at the `escalate` interrupt. A thread resolved some
other way (or a stale/corrupt entry) is pruned from the JSON file the
moment it's looked up, instead of `review`/`resume` trusting a cache that
could have silently drifted from what the graph engine actually knows.

## Notifications: Director's Telegram channel + email ticketing

Two independent, optional channels, both wired through one place
(`Director`, `agency/agents/director.py`) so `run.py`/`graph.py` never need
to know notifications exist:

- **Telegram is the Director's channel to the human** -- low-volume,
  high-signal only: an escalation the moment `run-all`/`loop` raises one
  (via `RunLedger`'s `on_event` hook, since every escalation already
  produces a `compose_done` ledger event with `escalated: true`), plus two
  on-demand reports:
  ```bash
  python -m agency.cli director-daily-report                       # digest of the run ledger + pending escalations
  python -m agency.cli director-monetization-report --summary "..."  # or --file path/to/summary.md
  ```
  Monetization reporting is deliberately bring-your-own: no revenue/
  analytics integration is wired in (Postiz's public API doesn't document a
  verified monetization endpoint, and no accounting system was specified),
  so this relays whatever text you give it rather than fabricating a
  computation. Schedule `director-daily-report` the same way as `run-all`
  (cron/systemd timer) for an actual daily habit.
- **Email tickets are the ops channel** -- everything DevOps/an on-call
  human would want tracked but not paged for: cloud-fallback usage,
  local-inference outages, ambiguous-publish skips (see the idempotency
  guard above), and `admin-report`/`devops-health` findings. `TicketRegistry`
  (`agency/notifications/tickets.py`) is a JSON index keyed by a dedup key,
  so repeat occurrences of the same problem bump one ticket's occurrence
  count instead of spawning a new one every run; `EmailTicketNotifier`
  sends real SMTP (stdlib `smtplib`/`email`, any provider) with proper
  `Message-ID`/`In-Reply-To`/`References` headers so a ticket's updates
  thread as one conversation in any real mail client -- a lightweight,
  self-hosted stand-in for a ticketing SaaS, not a fabricated integration
  with a vendor that was never named.

Both channels are configured independently in `.env`
(`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `SMTP_HOST`/etc. -- see
`.env.example`) and are safe no-ops when unset. A send failure on either
channel (unreachable SMTP host, dead bot token) is caught and logged to
stderr rather than propagating -- the same best-effort principle as
`illustrate_node`'s ComfyUI call: a notification failing must never break
the real work it's attached to.

## Running the whole agency (multiple customers/brands, on a schedule)

```bash
python -m agency.cli run-all                                    # one pass over every active brand in every org/*.yaml customer
python -m agency.cli run-all --dry-run                           # same, but never calls Postiz
python -m agency.cli run-all --with-images                       # also render + attach an Artist/ComfyUI image per post
python -m agency.cli run-all --only acme/widgets --only acme/gadgets  # restrict to specific brands, ignoring `active`
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

## Deploying Postiz + WordPress staging

`deploy/README.md` has the full Quadlet setup for running Postiz's own
stack (it needs Postgres, Redis, and -- since v2.12 -- Temporal with its own
Postgres and Elasticsearch, 6 containers total) and a WordPress staging
site alongside the agency, generated from and grounded against this
project's actual discovered host environment: which networking mode, which
drive holds the data, which ports were already taken by other running
services (Ollama/ComfyUI/Paperless/Khoj/n8n and others), and why WordPress
specifically needed a different networking approach than the rest of the
stack (Apache's hardcoded port 80 vs. rootless Podman's privileged-port
restriction). Re-run `deploy/discover_environment.py` and regenerate this
section's decisions if deploying to a different host.

## Tests

```bash
python -m pytest
```

Postiz and LLM calls are mocked (`respx`, fakes); LangGraph checkpointing
uses `MemorySaver` instead of the SQLite backend. No live Postiz instance or
model runner is required to run the suite.

## Roadmap (not yet built)

- `draft --with-image`/`run-all --with-images` cover the single-image case
  (Artist, default workflow style, topic as the brief) -- `VideoMaster`,
  `MusicAgent`, and `StudioWorker` output are still standalone
  (`animate`/`compose`/`assemble`), not attachable to a Postiz post.
- WordPress category/tag selection is manual (`create_post()` takes IDs
  looked up via `list_categories()`/`list_tags()`) -- no agent maps a
  brief to taxonomy yet. `write-publication --publish-to-wordpress` is
  also standalone, not part of `run-all`.
- Every role from the original expanded scope now exists in some form;
  `admin-report`/`devops-*` are standalone CLI commands, not yet folded
  into `run-all`'s scheduled cycle (e.g. an automatic weekly backup on a
  timer, rather than only ever run by hand).
- A search-capable backend for `RnDAgent` so it can genuinely track new
  tooling instead of relying on the model's training data alone.
- Monetization reporting has no real data source wired in yet --
  `director-monetization-report` relays bring-your-own text until a real
  revenue/analytics integration exists (see Notifications above).
- Feedback loop from Postiz analytics (`GET /public/v1/analytics/:integration`)
  back into the content agent's and topic agent's prompts.
- Embedding-based retrieval in `KnowledgeBase` instead of keyword overlap,
  once corpora get large.
- Per-brand/project run cadence (right now every customer in `org/` gets the
  same interval from `loop`/the systemd timer).
