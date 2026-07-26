import argparse
import sys

from agency.agents.content_agent import ContentAgent
from agency.agents.supervisor_agent import SupervisorAgent
from agency.brand import load_brand
from agency.config import Settings
from agency.inference.provider import default_provider
from agency.pipeline import run_pipeline
from agency.postiz.client import PostizClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency")
    parser.add_argument("--brand", required=True, help="Path to a brand profile YAML file")
    parser.add_argument("--topic", required=True, help="What the post should be about")
    parser.add_argument(
        "--publish",
        choices=["draft", "schedule", "now", "update"],
        default="draft",
        help="Postiz post type to create once approved (default: draft, i.e. nothing goes live)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the content + supervisor agents but never call the Postiz API",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    brand = load_brand(args.brand)
    provider = default_provider(settings)

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


if __name__ == "__main__":
    main()
