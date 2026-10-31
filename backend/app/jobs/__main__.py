"""Run a background job: python -m app.jobs <name>"""

import asyncio
import json
import sys

from app import audit
from app.db import SessionLocal


async def audit_verify() -> int:
    async with SessionLocal() as db:
        broken_at = await audit.verify(db)
    print(json.dumps({"job": "audit_verify", "intact": broken_at is None, "broken_at": broken_at}))
    return 0 if broken_at is None else 1


JOBS = {"audit_verify": audit_verify}


def main(argv: list[str]) -> int:
    return asyncio.run(JOBS[argv[0]]())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
