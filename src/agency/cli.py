import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from agency.agents.artist import Artist
from agency.agents.content_agent import ContentAgent
from agency.agents.designer import Designer
from agency.agents.ghost_writer import GhostWriter, render_publication_markdown, slugify
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.music_agent import MusicAgent
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.agents.video_master import VideoMaster
from agency.comfyui.client import ComfyUIClient
from agency.config import Settings
from agency.escalations import EscalationRegistry
from agency.graph import build_post_graph, initial_post_state
from agency.inference.provider import default_provider
from agency.knowledge import KnowledgeBase
from agency.org import brand_context, discover_customers, find_brand, find_project, load_customer
from agency.postiz.client import PostizClient
from agency.run import resume_escalation, run_all


@contextmanager
def _checkpointer(settings: Settings):
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        yield saver


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

    provider = default_provider(settings)
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
        ctx, args.brief, assets_dir, style=style, negative_prompt=negative_prompt, checkpoint=checkpoint, seed=args.seed
    )
    comfyui_client.close()

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

    provider = default_provider(settings)
    comfyui_client = ComfyUIClient(settings.comfyui_base_url)
    video_master = VideoMaster(provider, comfyui_client, settings.video_workflows_dir)

    assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "videos"
    saved = video_master.generate(
        ctx,
        args.brief,
        assets_dir,
        style=args.style,
        checkpoint=args.checkpoint,
        seed=args.seed,
        output_key=args.output_key,
    )
    comfyui_client.close()

    for path in saved:
        print(path)


def _run_write_publication(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)

    provider = default_provider(settings)
    designer = None if args.no_illustrations else Designer(provider)
    comfyui_client = None
    artist = None
    if not args.no_illustrations:
        comfyui_client = ComfyUIClient(settings.comfyui_base_url)
        artist = Artist(provider, comfyui_client, settings.workflows_dir)

    assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "images"
    ghost_writer = GhostWriter(provider, designer=designer, artist=artist)
    publication = ghost_writer.write_publication(
        ctx,
        args.brief,
        assets_dir,
        chapter_count=args.chapters,
        illustrate_chapters=args.illustrate_chapters,
        available_styles=[] if args.no_illustrations else _available_styles(settings.workflows_dir),
    )
    if comfyui_client is not None:
        comfyui_client.close()

    content_dir = Path(settings.content_root) / customer.slug / brand.slug
    content_dir.mkdir(parents=True, exist_ok=True)
    output_path = content_dir / f"{slugify(publication.title)}.md"
    output_path.write_text(render_publication_markdown(publication))
    print(output_path)


def _run_compose(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)

    provider = default_provider(settings)
    comfyui_client = ComfyUIClient(settings.comfyui_base_url)
    music_agent = MusicAgent(provider, comfyui_client, settings.audio_workflows_dir)

    assets_dir = Path(settings.assets_root) / customer.slug / brand.slug / "generated" / "audio"
    saved = music_agent.generate(ctx, args.brief, assets_dir, style=args.style, seed=args.seed)
    comfyui_client.close()

    for path in saved:
        print(path)


def _run_draft(args: argparse.Namespace, settings: Settings) -> None:
    customer = load_customer(args.org)
    brand = find_brand(customer, args.brand)
    project = find_project(brand, args.project) if args.project else None
    ctx = brand_context(brand, project)

    provider = default_provider(settings)
    knowledge_base = KnowledgeBase(customer.slug, brand.slug, settings.knowledge_root)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)

    with _checkpointer(settings) as checkpointer:
        graph = build_post_graph(
            ContentAgent(provider), SupervisorAgent(provider), postiz_client, knowledge_base
        ).compile(checkpointer=checkpointer)

        thread_id = f"{customer.slug}:{brand.slug}:{project.slug if project else '_default'}:cli"
        state = initial_post_state(ctx, args.topic, brand.integration_ids, brand.postiz_group_id, args.publish)
        output = graph.invoke(state, {"configurable": {"thread_id": thread_id}})

    print("--- Draft ---")
    print(output.get("draft", ""))
    print("\n--- Supervisor verdicts ---")
    for i, verdict in enumerate(output.get("verdicts", []), 1):
        print(f"[{i}] approved={verdict['approved']} reason={verdict['reason']}")

    if "__interrupt__" in output:
        print(f"\nEscalated for human review. Resume with: agency resume --thread-id {thread_id}", file=sys.stderr)
        sys.exit(2)
    if not output.get("approved"):
        print("\nRejected; nothing sent to Postiz.", file=sys.stderr)
        sys.exit(1)
    if output.get("postiz_response") is not None:
        print("\n--- Postiz response ---")
        print(output["postiz_response"])


def _run_batch(args: argparse.Namespace, settings: Settings) -> None:
    org_dir = Path(args.org_dir or settings.org_dir)
    customers = discover_customers(org_dir)
    if not customers:
        print(f"No customer files found in {org_dir}", file=sys.stderr)
        return

    provider = default_provider(settings)
    knowledge_agent = KnowledgeAgent(settings.knowledge_root)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)
    escalations = EscalationRegistry(settings.state_root)

    try:
        with _checkpointer(settings) as checkpointer:
            results = run_all(
                customers,
                settings.knowledge_root,
                settings.state_root,
                checkpointer,
                knowledge_agent,
                TopicAgent(provider),
                ContentAgent(provider),
                SupervisorAgent(provider),
                postiz_client,
                escalations,
            )
    finally:
        knowledge_agent.close()
        if postiz_client is not None:
            postiz_client.close()

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
    pending = escalations.list()
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
    entry = escalations.get(args.thread_id)
    if entry is None:
        print(f"No pending escalation with thread id {args.thread_id}", file=sys.stderr)
        sys.exit(1)

    provider = default_provider(settings)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)

    with _checkpointer(settings) as checkpointer:
        output = resume_escalation(
            checkpointer,
            escalations,
            ContentAgent(provider),
            SupervisorAgent(provider),
            postiz_client,
            args.thread_id,
            approved=args.approve,
            text=args.text,
            reason=args.reason,
        )

    if output.get("postiz_response") is not None:
        print("--- Postiz response ---")
        print(output["postiz_response"])
    else:
        print(f"Resumed {args.thread_id}: approved={output.get('approved')}")


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

    run_all_parser = subparsers.add_parser(
        "run-all", help="Ingest + propose topics + draft + supervise + publish for every customer/brand/project"
    )
    run_all_parser.add_argument(
        "--org-dir", default=None, help="Directory of customer YAML files (default: ORG_DIR env / ./org)"
    )
    run_all_parser.add_argument("--dry-run", action="store_true")

    loop_parser = subparsers.add_parser("loop", help="Run `run-all` repeatedly forever, sleeping between cycles")
    loop_parser.add_argument("--org-dir", default=None)
    loop_parser.add_argument("--dry-run", action="store_true")
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


if __name__ == "__main__":
    main()
