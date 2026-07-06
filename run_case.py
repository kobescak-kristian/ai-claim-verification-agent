#!/usr/bin/env python3
"""CLI entrypoint: run the bounded claim-verification agent on one eval case.

Usage:
    python run_case.py case_01_supported_wireless_earbuds
"""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from agent.harness import run_case  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python run_case.py <case_id>", file=sys.stderr)
        sys.exit(1)
    case_id = sys.argv[1]
    report = asyncio.run(run_case(case_id))
    print(report)


if __name__ == "__main__":
    main()
