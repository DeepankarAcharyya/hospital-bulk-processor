#!/usr/bin/env python3
"""Sample client for the hospital bulk processor API."""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# BASE_URL = "http://localhost:8000"
BASE_URL = "https://hospital-bulk-processor-6466.onrender.com"


def check_health(client: httpx.Client) -> None:
    resp = client.get("/")
    resp.raise_for_status()
    print(f"[health] {resp.json()}")


def bulk_upload(client: httpx.Client, csv_path: Path) -> str:
    """POST CSV and return batch_id."""
    with open(csv_path, "rb") as f:
        resp = client.post(
            "/hospitals/bulk",
            files={"file": (csv_path.name, f, "text/csv")},
            timeout=60,
        )

    data = resp.json()

    if resp.status_code == 422:
        print("[validation errors]")
        for err in data.get("errors", []):
            print(f"  row {err['row']}: {err['error']}")
        sys.exit(1)

    resp.raise_for_status()
    return data["batch_id"]


def poll_progress(client: httpx.Client, batch_id: str, poll_interval: float = 2.0) -> dict:
    """Poll GET /hospitals/batch/{batch_id}/progress until terminal state."""
    terminal = {"completed", "failed"}
    while True:
        resp = client.get(f"/hospitals/batch/{batch_id}/progress")
        if resp.status_code == 404:
            print(f"[error] batch_id={batch_id} not found")
            sys.exit(1)
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        suffix = " (circuit breaker cooling down)" if status == "cooldown" else ""
        print(f"  [{status}]{suffix} processed={data['processed_hospitals']}/{data['total_hospitals']} failed={data['failed_hospitals']}")
        if status in terminal:
            return data
        time.sleep(poll_interval)


def print_results(data: dict) -> None:
    print(f"\n{'='*50}")
    print(f"batch_id          : {data['batch_id']}")
    print(f"status            : {data['status']}")
    print(f"total_hospitals   : {data['total_hospitals']}")
    print(f"processed         : {data['processed_hospitals']}")
    print(f"failed            : {data['failed_hospitals']}")
    print(f"batch_activated   : {data['batch_activated']}")
    if data.get("error_message"):
        print(f"error             : {data['error_message']}")
    print(f"{'='*50}\n")


def main():
    parser = argparse.ArgumentParser(description="Hospital bulk processor test client")
    parser.add_argument("--url", default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    parser.add_argument(
        "--csv",
        default=Path(__file__).parent / "hospitals.csv",
        type=Path,
        help="Path to CSV file (default: hospitals.csv in this directory)",
    )
    parser.add_argument(
        "--batch-id",
        help="Poll an existing batch by ID (skips upload)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between progress polls (default: 2.0)",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    args = parser.parse_args()

    with httpx.Client(base_url=args.url) as client:
        check_health(client)

        if args.batch_id:
            print(f"\n[polling] batch_id={args.batch_id}")
            data = poll_progress(client, args.batch_id, poll_interval=args.poll_interval)
        else:
            if not args.csv.exists():
                print(f"[error] CSV not found: {args.csv}")
                sys.exit(1)

            print(f"\n[uploading] {args.csv} → {args.url}/hospitals/bulk")
            batch_id = bulk_upload(client, args.csv)
            print(f"[accepted] batch_id={batch_id}\n[polling]")

            data = poll_progress(client, batch_id, poll_interval=args.poll_interval)

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print_results(data)


if __name__ == "__main__":
    main()
