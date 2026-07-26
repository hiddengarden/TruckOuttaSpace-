import argparse
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from agency.agents.artist import Artist
from agency.agents.content_agent import ContentAgent
from agency.agents.designer import Designer
from agency.agents.devops import DevOps
from agency.agents.director import Director
from agency.agents.ghost_writer import GhostWriter, render_publication_markdown, slugify
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.music_agent import MusicAgent
from agency.agents.rnd_agent import RnDAgent
from agency.agents.secretary import Secretary
from agency.agents.studio_worker import StudioWorker
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.agents.video_master import VideoMaster
from agency.comfyui.client import ComfyUIClient
from agency.comfyui.workflow import validate_workflow_mapping
from agency.config import Settings
from agency.escalations import EscalationRegistry
from agency.graph import build_compose_graph, build_finalize_graph, initial_compose_state, initial_finalize_input
from agency.inference.provider import LocalInferenceUnavailable, default_provider
from agency.knowledge import KnowledgeBase
from agency.ledger import RunLedger
from agency.notifications.email_notifier import EmailTicketNotifier
from agency.notifications.telegram import TelegramNotifier
from agency.notifications.tickets import TicketRegistry
from agency.org import brand_context, discover_customers, find_brand, find_project, load_customer
from agency.postiz.client import PostizClient
from agency.run import resume_escalation, run_all
from agency.wordpress.client import WordPressClient
from agency.wordpress.publish import publish_publication_to_wordpress


@contextmanager
def _checkpointer(settings: Settings):
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        yield saver


def _director_from_settings(settings: Settings) -> Director:
    """Both channels are optional and independently configured: Telegram
    needs a bot token + chat id (BotFather issues the token), email tickets
    need an SMTP host. Either or both missing degrades to a safe no-op, not
    a crash -- notifications are an enhancement, not a hard dependency."""
    telegram = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    tickets, email = None, None
    if settings.smtp_host:
        tickets = TicketRegistry(settings.state_root)
        email = EmailTicketNotifier(
            settings.smtp_host, settings.smtp_port, settings.smtp_username, settings.smtp_password,
            settings.smtp_from_addr, settings.smtp_to_addr, use_tls=settings.smtp_use_tls,
        )
    return Director(telegram=telegram, tickets=tickets, email=email)


def _ledger(settings: Settings, director: Director) -> RunLedger:
    return RunLedger(settings.state_root, on_event=director.handle_ledger_event)


def _provider_for_brand(settings: Settings, ledger: RunLedger, brand_slug: str, allow_cloud_fallback: bool):
    def on_fallback(detail: str) -> None:
        ledger.record("fallback_used", brand=brand_slug, detail=detail)

    return default_provider(settings, allow_fallback=allow_cloud_fallback, on_fallback=on_fallback)


def _fail_on_local_inference_unavailable(brand_slug: str, exc: LocalInferenceUnavailable) -> None:
    print(
        f"Local inference unavailable and brand '{brand_slug}' has allow_cloud_fallback: false "
        f"(set it true in org config to allow OpenRouter fallback): {exc}",
        file=sys.stderr,
    )
    sys.exit(1)


def _run_ingest(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    agent = KnowledgeAgent(settings.knowledge_root)
    try:
        for url in args.urls:
            try:
                doc = agent.ingest_url(customer.slug, brand.slug, url)
                print(f"ingested {url} -> {doc.markdown_path} ({len(doc.asset_paths)} assets)")
            except RobotsDisallowed as exc:
                print(f"skipped {url}: {exc}", file=sys.stderr)
    finally:
        agent.close()


def _available_styles(workflows_dir: str | Path) -> list[str]:
    directory = Path(workflows_dir)
    return sorted(p.stem for p in directory.glob("*.json") if not p.name.endswith(".mapping.json"))


def _run_illustrate(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    try:
        provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
        designer = Designer(provider)
        comfyui_client = ComfyUIClient(settings.comfyui_base_url)
        artist = Artist(provider, comfyui_client, settings.workflows_dir)

        style, checkpoint, negative_prompt = args.style, args.checkpoint, ""
        if style is None:
            rec = designer.recommend_style(ctx, args.brief, _available_styles(settings.workflows_dir))
            style = rec.style
            checkpoint = checkpoint or rec.checkpoint
            negative_prompt = rec.negative_prompt
            print(f"Designer recommended style={style!r} checkpoint={checkpoint!r}", file=sys.stderr)

        assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"
        saved = artist.generate(
            ctx, args.brief, assets_dir, style=style, negative_prompt=negative_prompt, checkpoint=checkpoint,
            seed=args.seed,
        )
        comfyui_client.close()
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return

    for path in saved:
        print(path)
        if not args.no_review:
            verdict = designer.review_asset(ctx, args.brief, path)
            print(f"  Designer review: approved={verdict.approved} reason={verdict.reason}", file=sys.stderr)


def _run_animate(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    try:
        provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
        comfyui_client = ComfyUIClient(settings.comfyui_base_url)
        video_master = VideoMaster(provider, comfyui_client, settings.video_workflows_dir)

        assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "videos"
        saved = video_master.generate(
            ctx, args.brief, assets_dir, style=args.style, checkpoint=args.checkpoint, seed=args.seed,
            output_key=args.output_key,
        )
        comfyui_client.close()
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return

    for path in saved:
        print(path)


def _run_write_publication(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    try:
        provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
        designer = None if args.no_illustrations else Designer(provider)
        comfyui_client = None
        artist = None
        if not args.no_illustrations:
            comfyui_client = ComfyUIClient(settings.comfyui_base_url)
            artist = Artist(provider, comfyui_client, settings.workflows_dir)

        assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"
        ghost_writer = GhostWriter(provider, designer=designer, artist=artist)
        publication = ghost_writer.write_publication(
            ctx, args.brief, assets_dir, chapter_count=args.chapters, illustrate_chapters=args.illustrate_chapters,
            available_styles=[] if args.no_illustrations else _available_styles(settings.workflows_dir),
        )
        if comfyui_client is not None:
            comfyui_client.close()
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return

    content_dir = Path(settings.content_root) / customer.slug / brand.slug
    content_dir.mkdir(parents=True, exist_ok=True)
    output_path = content_dir / f"{slugify(publication.title)}.md"
    output_path.write_text(render_publication_markdown(publication))
    print(output_path)

    if args.publish_to_wordpress is not None:
        if brand.wordpress is None:
            print(f"Brand '{brand.slug}' has no `wordpress:` config; skipping", file=sys.stderr)
            return
        app_password = os.environ.get(brand.wordpress.app_password_env, "")
        if not app_password:
            print(f"Env var {brand.wordpress.app_password_env} is not set; skipping", file=sys.stderr)
            return
        wp_client = WordPressClient(brand.wordpress.base_url, brand.wordpress.username, app_password)
        result = publish_publication_to_wordpress(wp_client, publication, status=args.publish_to_wordpress)
        wp_client.close()
        print(f"WordPress: {result.get('link', result)}")


def _run_compose(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    try:
        provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
        comfyui_client = ComfyUIClient(settings.comfyui_base_url)
        music_agent = MusicAgent(provider, comfyui_client, settings.audio_workflows_dir)

        assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "audio"
        saved = music_agent.generate(ctx, args.brief, assets_dir, style=args.style, seed=args.seed)
        comfyui_client.close()
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return

    for path in saved:
        print(path)


def _run_assemble(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    images_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"
    image_paths = [Path(p) for p in args.image] if args.image else sorted(images_dir.glob("*.png")) + sorted(
        images_dir.glob("*.jpg")
    )
    if not image_paths:
        print(f"No images found (pass --image or generate some into {images_dir})", file=sys.stderr)
        sys.exit(1)

    try:
        designer = None
        if not args.no_review:
            designer = Designer(_provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback))
        worker = StudioWorker(designer=designer)

        output_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "videos"
        video_path, verdict = worker.assemble_and_review(
            ctx, args.brief, image_paths, output_dir, audio_path=args.audio, seconds_per_image=args.seconds_per_image
        )
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return

    print(video_path)
    if verdict is not None:
        print(f"Designer review: approved={verdict.approved} reason={verdict.reason}", file=sys.stderr)


def _run_admin_report(args: argparse.Namespace, settings: Settings) -> None:
    director = _director_from_settings(settings)
    customer = load_customer(args.org)
    postiz_client = PostizClient(settings.postiz_base_url, settings.postiz_api_key)
    secretary = Secretary(postiz_client=postiz_client)

    findings = secretary.run_all_checks(customer)
    postiz_client.close()

    if not findings:
        print("No admin findings.")
        return
    for finding in findings:
        label = finding.brand_slug + (f"/{finding.project_slug}" if finding.project_slug else "")
        print(f"[{finding.severity}] {label}: {finding.message}")
        if finding.severity in ("warning", "overdue"):
            director.open_ticket(
                dedup_key=f"secretary:{customer.slug}:{label}:{finding.category}",
                category=finding.category,
                severity="critical" if finding.severity == "overdue" else "warning",
                summary=f"{label}: {finding.message}",
                detail={"customer": customer.slug, "brand": finding.brand_slug, "project": finding.project_slug},
            )


def _run_devops_health(settings: Settings) -> None:
    director = _director_from_settings(settings)
    devops = DevOps(settings)
    for result in devops.check_service_health():
        print(f"{result.name}: {'OK' if result.ok else 'DOWN'} ({result.detail})")
        if not result.ok:
            director.open_ticket(
                dedup_key=f"devops:service:{result.name}",
                category="service-health",
                severity="critical",
                summary=f"{result.name} unreachable",
                detail={"service": result.name, "detail": result.detail},
            )
    env_result = devops.check_env_file_permissions()
    print(f"env_permissions: {'OK' if env_result.ok else 'WARN'} ({env_result.detail})")
    if not env_result.ok:
        director.open_ticket(
            dedup_key="devops:env_permissions",
            category="security",
            severity="warning",
            summary="`.env` file is world-readable",
            detail={"detail": env_result.detail},
        )


def _run_devops_backup(args: argparse.Namespace, settings: Settings) -> None:
    roots = [settings.org_dir, settings.knowledge_root, settings.state_root, settings.assets_root, settings.content_root]
    archive_path = DevOps(settings).backup(roots, args.backup_dir)
    print(archive_path)


def _run_devops_rnd(args: argparse.Namespace, settings: Settings) -> None:
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)
    devops = DevOps(settings)
    try:
        provider = default_provider(
            settings, allow_fallback=args.allow_cloud_fallback,
            on_fallback=lambda d: ledger.record("fallback_used", command="devops-rnd", detail=d),
        )
        print(devops.consult_rnd(RnDAgent(provider), focus=args.focus))
    except LocalInferenceUnavailable as exc:
        print(f"Local inference unavailable (pass --allow-cloud-fallback to allow OpenRouter): {exc}", file=sys.stderr)
        sys.exit(1)


def _run_draft(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)

    provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
    knowledge_base = KnowledgeBase(customer.slug, brand.slug, settings.knowledge_root)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)

    comfyui_client = None
    artist = None
    image_assets_dir = None
    if args.with_image:
        comfyui_client = ComfyUIClient(settings.comfyui_base_url)
        artist = Artist(provider, comfyui_client, settings.workflows_dir)
        image_assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"

    thread_id = f"{customer.slug}:{brand.slug}:{project.slug if project else '_default'}:cli"
    finalize_output: dict = {}

    try:
        with _checkpointer(settings) as checkpointer:
            compose_graph = build_compose_graph(ContentAgent(provider), SupervisorAgent(provider), knowledge_base)
            compose_graph = compose_graph.compile(checkpointer=checkpointer)
            output = compose_graph.invoke(
                initial_compose_state(ctx, args.topic), {"configurable": {"thread_id": thread_id}}
            )

            print("--- Draft ---")
            print(output.get("draft", ""))
            print("\n--- Supervisor verdicts ---")
            for i, verdict in enumerate(output.get("verdicts", []), 1):
                print(f"[{i}] approved={verdict['approved']} reason={verdict['reason']}")

            if "__interrupt__" in output:
                print(
                    f"\nEscalated for human review. Resume with: agency resume --thread-id {thread_id}",
                    file=sys.stderr,
                )
                sys.exit(2)

            image_brief = args.topic if args.with_image else None
            finalize_graph = build_finalize_graph(postiz_client, artist, image_assets_dir).compile(
                checkpointer=checkpointer
            )
            finalize_output = finalize_graph.invoke(
                initial_finalize_input(brand.integration_ids, brand.postiz_group_id, args.publish, image_brief),
                {"configurable": {"thread_id": thread_id}},
            )
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return
    finally:
        if comfyui_client is not None:
            comfyui_client.close()

    if finalize_output.get("postiz_response") is not None:
        print("\n--- Postiz response ---")
        print(finalize_output["postiz_response"])


def _parse_only(values: list[str] | None) -> set[tuple[str, str]] | None:
    if not values:
        return None
    parsed = set()
    for value in values:
        customer_slug, _, brand_slug = value.partition("/")
        parsed.add((customer_slug, brand_slug))
    return parsed


def _run_batch(args: argparse.Namespace, settings: Settings) -> None:
    org_dir = Path(args.org_dir or settings.org_dir)
    customers = discover_customers(org_dir)
    if not customers:
        print(f"No customer files found in {org_dir}", file=sys.stderr)
        return

    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)
    knowledge_agent = KnowledgeAgent(settings.knowledge_root)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)
    escalations = EscalationRegistry(settings.state_root)
    comfyui_client = ComfyUIClient(settings.comfyui_base_url) if args.with_images else None

    def provider_factory(allow_cloud_fallback: bool):
        def on_fallback(detail: str) -> None:
            ledger.record("fallback_used", detail=detail)

        return default_provider(settings, allow_fallback=allow_cloud_fallback, on_fallback=on_fallback)

    try:
        with _checkpointer(settings) as checkpointer:
            results = run_all(
                customers,
                settings.knowledge_root,
                settings.state_root,
                checkpointer,
                knowledge_agent,
                provider_factory,
                postiz_client,
                escalations,
                ledger,
                settings.ollama_base_url,
                settings.ollama_model,
                comfyui_client=comfyui_client,
                workflows_dir=settings.workflows_dir,
                assets_root=settings.assets_root,
                only=_parse_only(args.only),
            )
    finally:
        knowledge_agent.close()
        if postiz_client is not None:
            postiz_client.close()
        if comfyui_client is not None:
            comfyui_client.close()

    for result in results:
        label = f"{result.customer_slug}/{result.brand_slug}"
        if result.project_slug:
            label += f"/{result.project_slug}"
        print(f"=== {label} ===")
        for error in result.ingest_errors:
            print(f"  ingest error: {error}", file=sys.stderr)
        for outcome in result.outcomes:
            print(f"  [{outcome.status}] {outcome.topic} (thread {outcome.thread_id})")


def _run_loop(args: argparse.Namespace, settings: Settings) -> None:
    interval = args.interval_seconds or settings.run_interval_seconds
    while True:
        try:
            _run_batch(args, settings)
        except Exception as exc:  # a single bad cycle must not kill the scheduler
            print(f"run-all cycle failed: {exc}", file=sys.stderr)
        time.sleep(interval)


def _run_review(settings: Settings) -> None:
    escalations = EscalationRegistry(settings.state_root)
    with _checkpointer(settings) as checkpointer:
        pending = escalations.list_verified(checkpointer)
    if not pending:
        print("No pending escalations.")
        return
    for entry in pending:
        print(f"--- {entry['thread_id']} ---")
        print(f"  {entry['customer']}/{entry['brand']}" + (f"/{entry['project']}" if entry.get("project") else ""))
        print(f"  topic: {entry['topic']}")
        print(f"  reason: {entry['reason']}")
        print(f"  draft: {entry['draft']}")


def _run_resume(args: argparse.Namespace, settings: Settings) -> None:
    escalations = EscalationRegistry(settings.state_root)
    director = _director_from_settings(settings)
    ledger = _ledger(settings, director)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)
    comfyui_client = None

    try:
        with _checkpointer(settings) as checkpointer:
            # Verified against the checkpoint, not just the JSON file: a
            # thread someone already resolved directly against the graph
            # (bypassing `agency resume`) must not be resumable a second
            # time here.
            entry = escalations.get_verified(args.thread_id, checkpointer)
            if entry is None:
                print(
                    f"No pending escalation with thread id {args.thread_id} "
                    "(already resolved, or never escalated)",
                    file=sys.stderr,
                )
                sys.exit(1)

            customer = next(
                (c for c in discover_customers(settings.org_dir) if c.slug == entry["customer"]), None
            )
            if customer is None:
                print(f"Customer '{entry['customer']}' not found under {settings.org_dir}", file=sys.stderr)
                sys.exit(1)
            brand = find_brand(customer, entry["brand"])

            provider = _provider_for_brand(settings, ledger, brand.slug, brand.allow_cloud_fallback)
            artist = None
            image_assets_dir = None
            if args.with_image:
                comfyui_client = ComfyUIClient(settings.comfyui_base_url)
                artist = Artist(provider, comfyui_client, settings.workflows_dir)
                image_assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"

            output = resume_escalation(
                checkpointer, escalations, ContentAgent(provider), SupervisorAgent(provider),
                args.thread_id, approved=args.approve, text=args.text, reason=args.reason,
            )

            if not output.get("approved"):
                print(f"Resumed {args.thread_id}: rejected ({output.get('rejected_reason')})")
                return

            image_brief = entry["topic"] if args.with_image else None
            finalize_graph = build_finalize_graph(postiz_client, artist, image_assets_dir).compile(
                checkpointer=checkpointer
            )
            finalize_output = finalize_graph.invoke(
                initial_finalize_input(brand.integration_ids, brand.postiz_group_id, args.publish, image_brief),
                {"configurable": {"thread_id": args.thread_id}},
            )
    except LocalInferenceUnavailable as exc:
        _fail_on_local_inference_unavailable(brand.slug, exc)
        return
    finally:
        if comfyui_client is not None:
            comfyui_client.close()

    if finalize_output.get("postiz_response") is not None:
        print("--- Postiz response ---")
        print(finalize_output["postiz_response"])
    else:
        print(f"Resumed {args.thread_id}: approved, but nothing published (dry-run or no postiz_client)")


def _run_director_daily_report(args: argparse.Namespace, settings: Settings) -> None:
    """Reads what already happened (the run ledger) and what's still
    outstanding (verified escalations) and pages the Director's Telegram
    channel with a digest -- no separate tracking of its own, so it can
    never drift from what run-all/draft actually recorded."""
    director = _director_from_settings(settings)
    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    entries = [e for e in RunLedger(settings.state_root).read_all() if datetime.fromisoformat(e["timestamp"]) >= since]
    counts = Counter(e["event"] for e in entries)

    escalations = EscalationRegistry(settings.state_root)
    with _checkpointer(settings) as checkpointer:
        pending = escalations.list_verified(checkpointer)

    published = sum(1 for e in entries if e.get("event") == "finalize_done" and e.get("published"))
    escalated = sum(1 for e in entries if e.get("event") == "compose_done" and e.get("escalated"))
    lines = [
        f"window: last {args.since_hours}h",
        f"composed: {counts.get('compose_done', 0)}",
        f"published: {published}",
        f"escalations raised: {escalated}",
        f"pending escalations now: {len(pending)}",
        f"local inference unavailable: {counts.get('local_inference_unavailable', 0)}",
        f"cloud fallback used: {counts.get('fallback_used', 0)}",
        f"ambiguous publishes skipped: {counts.get('finalize_ambiguous_skip', 0)}",
    ]
    text = "\n".join(lines)
    print(text)
    director.daily_summary(text)


def _run_director_monetization_report(args: argparse.Namespace, settings: Settings) -> None:
    """No revenue/analytics integration is wired in -- Postiz's public API
    doesn't document a verified monetization endpoint, and no accounting
    system was specified, so this is deliberately bring-your-own: point it
    at a file (e.g. exported from wherever you actually track revenue) or
    pass text directly, and it gets relayed to the Director's Telegram
    channel as-is."""
    director = _director_from_settings(settings)
    text = Path(args.file).read_text() if args.file else args.summary
    print(text)
    director.monetization_summary(text)


def _run_preflight(settings: Settings) -> None:
    """Sanity checks runnable before/without a real deployment: connectivity
    to already-configured services, required config presence, workflow/
    mapping consistency, and that org YAML actually parses -- all reusing
    existing, already-tested building blocks rather than new fabricated
    checks. Exits non-zero if anything looks wrong, so it's usable as a gate."""
    ok = True

    print("--- Service connectivity ---")
    devops = DevOps(settings)
    for result in devops.check_service_health():
        print(f"{result.name}: {'OK' if result.ok else 'DOWN'} ({result.detail})")
        ok = ok and result.ok

    env_result = devops.check_env_file_permissions()
    print(f"env_permissions: {'OK' if env_result.ok else 'WARN'} ({env_result.detail})")
    ok = ok and env_result.ok

    print("\n--- Required config ---")
    postiz_key_ok = bool(settings.postiz_api_key)
    print(f"POSTIZ_API_KEY: {'OK' if postiz_key_ok else 'MISSING'}")
    ok = ok and postiz_key_ok

    print("\n--- Notification channels (both optional) ---")
    telegram_configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    print(f"telegram (Director channel): {'configured' if telegram_configured else 'not configured'}")
    smtp_configured = bool(settings.smtp_host)
    print(f"email ticketing (SMTP): {'configured' if smtp_configured else 'not configured'}")

    print("\n--- ComfyUI workflow mappings ---")
    for label, directory in [
        ("image", settings.workflows_dir),
        ("video", settings.video_workflows_dir),
        ("audio", settings.audio_workflows_dir),
    ]:
        directory_path = Path(directory)
        if not directory_path.exists():
            print(f"{label}: {directory} does not exist (skipped)")
            continue
        styles = _available_styles(directory_path)
        if not styles:
            print(f"{label}: no workflows found under {directory}")
            continue
        for style in styles:
            problems = validate_workflow_mapping(directory_path, style)
            if problems:
                ok = False
                for problem in problems:
                    print(f"{label}/{style}: {problem}")
            else:
                print(f"{label}/{style}: OK")

    print("\n--- Org config ---")
    try:
        customers = discover_customers(settings.org_dir)
        print(f"org: {len(customers)} customer file(s) parsed OK under {settings.org_dir}")
    except Exception as exc:
        ok = False
        print(f"org: failed to parse customer files under {settings.org_dir}: {exc}")

    print("\n" + ("PREFLIGHT OK" if ok else "PREFLIGHT FAILED"))
    if not ok:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Scrape URLs into a brand's knowledge base")
    ingest_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    ingest_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    ingest_parser.add_argument("--url", action="append", required=True, dest="urls")

    illustrate_parser = subparsers.add_parser(
        "illustrate", help="Generate an image via a local ComfyUI instance and save it to the brand's asset bank"
    )
    illustrate_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    illustrate_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    illustrate_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    illustrate_parser.add_argument("--brief", required=True, help="What the image should depict")
    illustrate_parser.add_argument(
        "--style",
        default=None,
        help="Workflow name under workflows_dir; omit to let the Designer recommend one",
    )
    illustrate_parser.add_argument("--checkpoint", default=None, help="Override the model checkpoint filename")
    illustrate_parser.add_argument("--seed", type=int, default=None)
    illustrate_parser.add_argument(
        "--no-review", action="store_true", help="Skip the Designer's post-generation QC pass"
    )

    animate_parser = subparsers.add_parser(
        "animate", help="Generate a video via a local ComfyUI instance and save it to the brand's asset bank"
    )
    animate_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    animate_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    animate_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    animate_parser.add_argument("--brief", required=True, help="What the video should depict")
    animate_parser.add_argument(
        "--style", default="default", help="Workflow name under video_workflows_dir (default: 'default')"
    )
    animate_parser.add_argument("--checkpoint", default=None, help="Override the model checkpoint filename")
    animate_parser.add_argument("--seed", type=int, default=None)
    animate_parser.add_argument(
        "--output-key",
        default="images",
        help="History output key to collect ('images' for native SaveVideo, 'gifs' for VHS combine)",
    )

    compose_parser = subparsers.add_parser(
        "compose", help="Generate music/SFX via a local ComfyUI instance and save it to the brand's asset bank"
    )
    compose_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    compose_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    compose_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    compose_parser.add_argument("--brief", required=True, help="What the music/SFX should sound like")
    compose_parser.add_argument(
        "--style", default="default", help="Workflow name under audio_workflows_dir (default: 'default')"
    )
    compose_parser.add_argument("--seed", type=int, default=None)

    admin_report_parser = subparsers.add_parser(
        "admin-report", help="Secretary: deadlines + Postiz platform-compliance findings for one customer"
    )
    admin_report_parser.add_argument("--org", required=True, help="Path to a customer YAML file")

    subparsers.add_parser("devops-health", help="DevOps: check Postiz/Ollama/ComfyUI reachability + .env permissions")

    devops_backup_parser = subparsers.add_parser(
        "devops-backup", help="DevOps: tar.gz the org/knowledge/state/assets/content roots"
    )
    devops_backup_parser.add_argument("--backup-dir", default="./backups")

    devops_rnd_parser = subparsers.add_parser(
        "devops-rnd", help="DevOps consults R&D for pipeline improvement suggestions"
    )
    devops_rnd_parser.add_argument("--focus", default=None, help="Optional area to focus suggestions on")
    devops_rnd_parser.add_argument(
        "--allow-cloud-fallback", action="store_true", help="Allow OpenRouter if the local model is unreachable"
    )

    assemble_parser = subparsers.add_parser(
        "assemble", help="Assemble the brand's asset bank into a short/reel via ffmpeg (StudioWorker)"
    )
    assemble_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    assemble_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    assemble_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    assemble_parser.add_argument("--brief", required=True, help="What the assembled video is about")
    assemble_parser.add_argument(
        "--image", action="append", default=None, help="Explicit image path(s); default: the brand's asset bank"
    )
    assemble_parser.add_argument("--audio", default=None, help="Optional background audio track path")
    assemble_parser.add_argument("--seconds-per-image", type=float, default=3.0)
    assemble_parser.add_argument("--no-review", action="store_true", help="Skip the Designer's post-assembly QC pass")

    write_publication_parser = subparsers.add_parser(
        "write-publication", help="Write a long-form publication (book/course/ebook) as markdown"
    )
    write_publication_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    write_publication_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    write_publication_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    write_publication_parser.add_argument("--brief", required=True, help="What the publication is about")
    write_publication_parser.add_argument("--chapters", type=int, default=5)
    write_publication_parser.add_argument(
        "--illustrate-chapters", action="store_true", help="Also request an illustration per chapter, not just a cover"
    )
    write_publication_parser.add_argument(
        "--no-illustrations", action="store_true", help="Skip the cover/illustrations entirely (no ComfyUI needed)"
    )
    write_publication_parser.add_argument(
        "--publish-to-wordpress",
        choices=["draft", "publish", "pending", "future"],
        default=None,
        help="Also publish to the brand's configured WordPress site (requires `wordpress:` in its org YAML)",
    )

    draft_parser = subparsers.add_parser("draft", help="Draft, supervise, and optionally publish one post")
    draft_parser.add_argument("--org", required=True, help="Path to a customer YAML file")
    draft_parser.add_argument("--brand", required=True, help="Brand slug within that customer")
    draft_parser.add_argument("--project", default=None, help="Optional project slug within that brand")
    draft_parser.add_argument("--topic", required=True, help="What the post should be about")
    draft_parser.add_argument(
        "--publish",
        choices=["draft", "schedule", "now", "update"],
        default="draft",
        help="Postiz post type to create once approved (default: draft, i.e. nothing goes live)",
    )
    draft_parser.add_argument(
        "--dry-run", action="store_true", help="Run the agents but never call the Postiz API"
    )
    draft_parser.add_argument(
        "--with-image",
        action="store_true",
        help="Also generate an image (Artist, default ComfyUI workflow style) and attach it to the post",
    )

    run_all_parser = subparsers.add_parser(
        "run-all", help="Ingest + propose topics + draft + supervise + publish for every customer/brand/project"
    )
    run_all_parser.add_argument(
        "--org-dir", default=None, help="Directory of customer YAML files (default: ORG_DIR env / ./org)"
    )
    run_all_parser.add_argument("--dry-run", action="store_true")
    run_all_parser.add_argument(
        "--with-images", action="store_true", help="Also generate + attach an image to every post via Artist/ComfyUI"
    )
    run_all_parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Restrict to one customer/brand (e.g. acme/widgets); repeatable. Default: every active brand.",
    )

    loop_parser = subparsers.add_parser("loop", help="Run `run-all` repeatedly forever, sleeping between cycles")
    loop_parser.add_argument("--org-dir", default=None)
    loop_parser.add_argument("--dry-run", action="store_true")
    loop_parser.add_argument("--with-images", action="store_true")
    loop_parser.add_argument("--only", action="append", default=None)
    loop_parser.add_argument(
        "--interval-seconds", type=int, default=None, help="Default: RUN_INTERVAL_SECONDS env / 21600 (6h)"
    )

    subparsers.add_parser("review", help="List posts escalated for human review")

    resume_parser = subparsers.add_parser("resume", help="Approve or reject a post escalated for human review")
    resume_parser.add_argument("--thread-id", required=True)
    group = resume_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", dest="approve", action="store_false")
    resume_parser.add_argument("--text", default=None, help="Replacement text to publish (only with --approve)")
    resume_parser.add_argument("--reason", default=None, help="Why it was rejected (only with --reject)")
    resume_parser.add_argument("--dry-run", action="store_true")
    resume_parser.add_argument(
        "--with-image", action="store_true", help="Also generate an image and attach it when publishing"
    )
    resume_parser.add_argument(
        "--publish",
        choices=["draft", "schedule", "now", "update"],
        default="draft",
        help="Postiz post type once approved (default: draft)",
    )

    daily_report_parser = subparsers.add_parser(
        "director-daily-report", help="Director: digest of the run ledger + pending escalations, sent via Telegram"
    )
    daily_report_parser.add_argument(
        "--since-hours", type=int, default=24, help="How far back to summarize (default: 24)"
    )

    monetization_report_parser = subparsers.add_parser(
        "director-monetization-report",
        help="Director: relay a monetization/revenue summary via Telegram (bring-your-own data)",
    )
    monetization_group = monetization_report_parser.add_mutually_exclusive_group(required=True)
    monetization_group.add_argument("--summary", default=None, help="Summary text to send")
    monetization_group.add_argument("--file", default=None, help="Path to a file with the summary text")

    subparsers.add_parser(
        "preflight", help="Sanity-check service connectivity, config, workflow mappings, and org files"
    )

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "ingest":
        _run_ingest(args, settings)
    elif args.command == "illustrate":
        _run_illustrate(args, settings)
    elif args.command == "animate":
        _run_animate(args, settings)
    elif args.command == "compose":
        _run_compose(args, settings)
    elif args.command == "assemble":
        _run_assemble(args, settings)
    elif args.command == "admin-report":
        _run_admin_report(args, settings)
    elif args.command == "devops-health":
        _run_devops_health(settings)
    elif args.command == "devops-backup":
        _run_devops_backup(args, settings)
    elif args.command == "devops-rnd":
        _run_devops_rnd(args, settings)
    elif args.command == "write-publication":
        _run_write_publication(args, settings)
    elif args.command == "draft":
        _run_draft(args, settings)
    elif args.command == "run-all":
        _run_batch(args, settings)
    elif args.command == "loop":
        _run_loop(args, settings)
    elif args.command == "review":
        _run_review(settings)
    elif args.command == "resume":
        _run_resume(args, settings)
    elif args.command == "director-daily-report":
        _run_director_daily_report(args, settings)
    elif args.command == "director-monetization-report":
        _run_director_monetization_report(args, settings)
    elif args.command == "preflight":
        _run_preflight(settings)


if __name__ == "__main__":
    main()
