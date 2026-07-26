import argparse
import sys
import time
from pathlib import Path

from agency.agents.content_agent import ContentAgent
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.supervisor_agent import SupervisorAgent
from agency.agents.topic_agent import TopicAgent
from agency.batch import run_all
from agency.brand import BrandProfile, load_brand
from agency.config import Settings
from agency.inference.provider import default_provider
from agency.knowledge import KnowledgeBase
from agency.pipeline import run_pipeline
from agency.postiz.client import PostizClient


def _run_ingest(brand: BrandProfile, urls: list[str], settings: Settings) -> None:
    agent = KnowledgeAgent(settings.knowledge_root)
    try:
        for url in urls:
            try:
                doc = agent.ingest_url(brand, url)
                print(f"ingested {url} -> {doc.markdown_path} ({len(doc.asset_paths)} assets)")
            except RobotsDisallowed as exc:
                print(f"skipped {url}: {exc}", file=sys.stderr)
    finally:
        agent.close()


def _run_draft(brand: BrandProfile, args: argparse.Namespace, settings: Settings) -> None:
    provider = default_provider(settings)
    knowledge_base = KnowledgeBase(brand, settings.knowledge_root)

    postiz_client = None
    if not args.dry_run:
        postiz_client = PostizClient(settings.postiz_base_url, settings.postiz_api_key)

    result = run_pipeline(
        brand=brand,
        topic=args.topic,
        content_agent=ContentAgent(provider),
        supervisor_agent=SupervisorAgent(provider),
        postiz_client=postiz_client,
        post_type=args.publish,
        knowledge_base=knowledge_base,
    )

    print("--- Final text ---")
    print(result.final_text)
    print("\n--- Supervisor verdicts ---")
    for i, verdict in enumerate(result.verdicts, 1):
        print(f"[{i}] approved={verdict.approved} reason={verdict.reason}")

    if not result.approved:
        print("\nNot approved by supervisor; nothing sent to Postiz.", file=sys.stderr)
        sys.exit(1)

    if result.postiz_response is not None:
        print("\n--- Postiz response ---")
        print(result.postiz_response)


def _run_batch(args: argparse.Namespace, settings: Settings) -> None:
    brands_dir = Path(args.brands_dir or settings.brands_dir)
    brand_paths = sorted(brands_dir.glob("*.yaml"))
    if not brand_paths:
        print(f"No brand files found in {brands_dir}", file=sys.stderr)
        return

    provider = default_provider(settings)
    knowledge_agent = KnowledgeAgent(settings.knowledge_root)
    postiz_client = None if args.dry_run else PostizClient(settings.postiz_base_url, settings.postiz_api_key)

    try:
        results = run_all(
            brand_paths,
            settings.knowledge_root,
            settings.state_root,
            knowledge_agent,
            TopicAgent(provider),
            ContentAgent(provider),
            SupervisorAgent(provider),
            postiz_client,
        )
    finally:
        knowledge_agent.close()
        if postiz_client is not None:
            postiz_client.close()

    for result in results:
        print(f"=== {result.brand_slug} ===")
        for error in result.ingest_errors:
            print(f"  ingest error: {error}", file=sys.stderr)
        for topic, pipeline_result in zip(result.topics, result.pipeline_results):
            status = "published" if pipeline_result.approved else "rejected"
            print(f"  [{status}] {topic}")


def _run_loop(args: argparse.Namespace, settings: Settings) -> None:
    interval = args.interval_seconds or settings.run_interval_seconds
    while True:
        try:
            _run_batch(args, settings)
        except Exception as exc:  # a single bad cycle must not kill the scheduler
            print(f"run-all cycle failed: {exc}", file=sys.stderr)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Scrape URLs into a brand's knowledge base")
    ingest_parser.add_argument("--brand", required=True, help="Path to a brand profile YAML file")
    ingest_parser.add_argument("--url", action="append", required=True, dest="urls")

    draft_parser = subparsers.add_parser("draft", help="Draft, supervise, and optionally publish one post")
    draft_parser.add_argument("--brand", required=True, help="Path to a brand profile YAML file")
    draft_parser.add_argument("--topic", required=True, help="What the post should be about")
    draft_parser.add_argument(
        "--publish",
        choices=["draft", "schedule", "now", "update"],
        default="draft",
        help="Postiz post type to create once approved (default: draft, i.e. nothing goes live)",
    )
    draft_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the content + supervisor agents but never call the Postiz API",
    )

    run_all_parser = subparsers.add_parser(
        "run-all", help="Ingest + propose topics + draft + supervise + publish for every brand"
    )
    run_all_parser.add_argument(
        "--brands-dir", default=None, help="Directory of brand YAML files (default: BRANDS_DIR env / ./brands)"
    )
    run_all_parser.add_argument("--dry-run", action="store_true")

    loop_parser = subparsers.add_parser("loop", help="Run `run-all` repeatedly forever, sleeping between cycles")
    loop_parser.add_argument("--brands-dir", default=None)
    loop_parser.add_argument("--dry-run", action="store_true")
    loop_parser.add_argument(
        "--interval-seconds", type=int, default=None, help="Default: RUN_INTERVAL_SECONDS env / 21600 (6h)"
    )

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "ingest":
        _run_ingest(load_brand(args.brand), args.urls, settings)
    elif args.command == "draft":
        _run_draft(load_brand(args.brand), args, settings)
    elif args.command == "run-all":
        _run_batch(args, settings)
    elif args.command == "loop":
        _run_loop(args, settings)


if __name__ == "__main__":
    main()
