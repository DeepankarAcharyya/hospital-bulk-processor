#!/usr/bin/env python3
"""Sample client for the hospital bulk processor API."""

import argparse
import json
import sys
from pathlib import Path

import httpx

# BASE_URL = "http://localhost:8000"
BASE_URL = "https://hospital-bulk-processor-6466.onrender.com"


def check_health(client: httpx.Client) -> None:
    resp = client.get("/")
    resp.raise_for_status()
    print(f"[health] {resp.json()}")


def bulk_upload(client: httpx.Client, csv_path: Path) -> dict:
    with open(csv_path, "rb") as f:
        resp = client.post(
            "/hospitals/bulk",
            files={"file": (csv_path.name, f, "text/csv")},
            timeout=60,
        )

    data = resp.json()

    if resp.status_code == 422:
        print(f"[validation errors]")
        for err in data.get("errors", []):
            print(f"  row {err['row']}: {err['error']}")
        sys.exit(1)

    resp.raise_for_status()
    return data


def print_results(data: dict) -> None:
    print(f"\n{'='*50}")
    print(f"batch_id          : {data['batch_id']}")
    print(f"total_hospitals   : {data['total_hospitals']}")
    print(f"processed         : {data['processed_hospitals']}")
    print(f"failed            : {data['failed_hospitals']}")
    print(f"batch_activated   : {data['batch_activated']}")
    print(f"processing_time   : {data['processing_time_seconds']}s")
    print(f"{'='*50}")

    for h in data.get("hospitals", []):
        status_icon = "✓" if "activated" in h["status"] else ("✗" if h["status"] == "failed" else "·")
        print(f"  {status_icon} row {h['row']:>2} | id={h['hospital_id']:<6} | {h['status']:<24} | {h['name']}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Hospital bulk processor test client")
    parser.add_argument("--url", default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    parser.add_argument(
        "--csv",
        default=Path(__file__).parent / "hospitals.csv",
        type=Path,
        help="Path to CSV file (default: hospitals.csv in this directory)",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[error] CSV not found: {args.csv}")
        sys.exit(1)

    with httpx.Client(base_url=args.url) as client:
        check_health(client)

        print(f"\n[uploading] {args.csv} → {args.url}/hospitals/bulk")
        data = bulk_upload(client, args.csv)

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print_results(data)


if __name__ == "__main__":
    main()
