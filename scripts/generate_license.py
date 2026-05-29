from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time


def encode_payload(payload: dict[str, object], secret: str) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a local YTSubViewer license token.")
    parser.add_argument("--secret", required=True)
    parser.add_argument("--licensee", required=True)
    parser.add_argument("--plan", default="standard")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    now = int(time.time())
    payload = {
        "licensee": args.licensee,
        "plan": args.plan,
        "issued_at": now,
        "expires_at": now + args.days * 86400,
    }
    print(encode_payload(payload, args.secret))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
