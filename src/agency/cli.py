import argparse
import sys

from agency.agents.content_agent import ContentAgent
from agency.agents.knowledge_agent import KnowledgeAgent, RobotsDisallowed
from agency.agents.supervisor_agent import SupervisorAgent
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Scrape URLs into a brand's knowledge base")
    ingest_parser.add_argument("--brand", required=True, help="Path to a brand profile YAML file")
    ingest_parser.add_argument("--url", action="append", required=True, dest="urls")

    draft_parser = subparsers.add_parser("draft", help="Draft, supervise, and optionally publish a post")
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

    args = parser.parse_args()
    settings = Settings.from_env()
    brand = load_brand(args.brand)

    if args.command == "ingest":
        _run_ingest(brand, args.urls, settings)
    elif args.command == "draft":
        _run_draft(brand, args, settings)


if __name__ == "__main__":
    main()
