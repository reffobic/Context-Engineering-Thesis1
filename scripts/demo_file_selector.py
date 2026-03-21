#!/usr/bin/env python3
"""
Demo: File-level context selection (Dr. Ibrahim's idea).

Given a project file map (path + purpose only) and a user change request,
the selector returns the minimal set of files an LLM needs as context.

Usage:
  # Use mock LLM (no Ollama required)
  python scripts/demo_file_selector.py

  # Use local Ollama (must have: ollama run llama2 or similar)
  python scripts/demo_file_selector.py --ollama

  # Custom request
  python scripts/demo_file_selector.py --request "Use another vector database for the RAG"

  # Apache Subversion codebase (team-chosen for experiments)
  python scripts/demo_file_selector.py --project subversion
  python scripts/demo_file_selector.py --project subversion --request "Change or add a checksum backend"
"""

import argparse
import os
import sys

# Project root on path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.file_context import FileContextSelector, FileMap
from src.file_context.selector import OllamaClient, MockFileSelectorClient

# Gold set for image-upload request (from Claude's answer in the shared chat)
IMAGE_UPLOAD_GOLD_MUST = [
    "services/chatbot_context_builder.py",
    "utils/llm_client.py",
    "schemas/chat.py",
    "api/chatbot_sessions.py",
    "services/chatbot_chat_service.py",
    "models/chat_message.py",
    "utils/validators.py",
]
IMAGE_UPLOAD_GOLD_SHOULD = [
    "services/chatbot_file_service.py",
    "utils/storage.py",
    "models/chatbot_file.py",
    "config.py",
]

# Default request per project
DEFAULT_REQUESTS = {
    "ai": "Enable uploading an image to the custom chat.",
    "subversion": "What files are needed to change or add a checksum backend (e.g. xdigest)?",
}


def load_file_map(project: str) -> FileMap:
    if project == "subversion":
        from tests.fixtures.subversion_file_map import get_subversion_file_map
        return get_subversion_file_map()
    from tests.fixtures.ai_project_file_map import get_ai_project_file_map
    return get_ai_project_file_map()


def main():
    parser = argparse.ArgumentParser(description="File context selector demo")
    parser.add_argument(
        "--project",
        choices=["ai", "subversion"],
        default="ai",
        help="Project file map: ai (chatbot+RAG) or subversion (Apache Subversion)",
    )
    parser.add_argument(
        "--ollama",
        action="store_true",
        help="Use local Ollama (default: mock LLM)",
    )
    parser.add_argument(
        "--model",
        default="llama2",
        help="Ollama model name (default: llama2)",
    )
    parser.add_argument(
        "--request",
        default=None,
        help="User change request (default depends on --project)",
    )
    args = parser.parse_args()
    if args.request is None:
        args.request = DEFAULT_REQUESTS[args.project]

    file_map = load_file_map(args.project)
    client = OllamaClient(model=args.model) if args.ollama else MockFileSelectorClient()
    selector = FileContextSelector(llm_client=client)

    print("=" * 60)
    print("  FILE-LEVEL CONTEXT SELECTION (Context Optimization Agent)")
    print("=" * 60)
    print(f"  Project: {file_map.name} ({len(file_map.entries)} files)")
    print(f"  Request: {args.request}")
    print(f"  LLM: {'Ollama (' + args.model + ')' if args.ollama else 'Mock'}")
    print("=" * 60)

    result = selector.select(file_map, args.request)

    # print("\n  RAW LLM RESPONSE:")
    # print("  " + "-" * 50)
    # print(result.raw_response)

    print("\n  SELECTED FILES (minimal context set):")
    print("  " + "-" * 50)
    for p in result.selected_paths:
        print(f"    {p}")
    print("\n  REASONING:")
    print("  " + result.reasoning[:400] + ("..." if len(result.reasoning) > 400 else ""))

    # Compare to gold for the default request
    if "image" in args.request.lower() and "upload" in args.request.lower():
        gold_all = set(IMAGE_UPLOAD_GOLD_MUST) | set(IMAGE_UPLOAD_GOLD_SHOULD)
        selected_set = set(result.selected_paths)
        recall = len(selected_set & gold_all) / len(gold_all) if gold_all else 0
        extra = selected_set - gold_all
        missing = gold_all - selected_set
        print("\n  COMPARISON TO GOLD:")
        print("  " + "-" * 50)
        print(f"    Recall (selected ∩ gold / gold): {recall:.0%}")
        if missing:
            print(f"    Missing from gold: {sorted(missing)}")
        if extra:
            print(f"    Extra selected: {sorted(extra)}")

    print()


if __name__ == "__main__":
    main()
