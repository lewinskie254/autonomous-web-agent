"""
CLI entry point for the hybrid (DOM + confidence-gated screenshot) agent.

Usage:
    python run_hybrid_task.py --task "Search for a wireless mouse and list its price" \
        --url "https://example-shop.com" --website shopify_store_a
"""
import argparse
import json

from hybrid_agent import run_task


def main():
    parser = argparse.ArgumentParser(description="Run the hybrid DOM+vision web agent on a task.")
    parser.add_argument("--task", required=True, help="Natural-language task description")
    parser.add_argument("--url", required=True, help="Starting URL")
    parser.add_argument("--website", default=None, help="Label for this website, used for generalization grouping in the DB")
    args = parser.parse_args()

    result = run_task(args.task, args.url, website_name=args.website)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
