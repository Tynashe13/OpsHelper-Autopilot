"""
Test the REAL discovered Auto Chats API contract.

Usage: python test_auto_call.py

Real contract (reverse-engineered from the browser's own working call):
  POST https://auto-workflow-api.supervity.ai/api/v1/chats/{threadId}/messages
  multipart/form-data: message, locale, model
  threadId = the workflow's own ID (AUTO_LLM_JUDGMENT_WORKFLOW_ID)

The browser used a user JWT for auth on this call (no cookies needed
here, unlike the streaming endpoint). This test swaps in the Custom API
Key + x-source/x-active-org instead, to see if that's accepted the same
way. Then polls GET .../messages to read the reply back.
"""

import asyncio
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

CHATS_BASE_URL = "https://auto-workflow-api.supervity.ai/api/v1"


async def main():
    api_key = os.getenv("AUTO_API_KEY")
    org_key = os.getenv("AUTO_ORG_KEY")
    thread_id = os.getenv("AUTO_LLM_JUDGMENT_WORKFLOW_ID")

    missing = [n for n, v in [("AUTO_API_KEY", api_key), ("AUTO_ORG_KEY", org_key), ("AUTO_LLM_JUDGMENT_WORKFLOW_ID", thread_id)] if not v]
    if missing:
        print("CONFIG ERROR: missing", missing)
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-source": "external",
        "x-active-org": org_key,
        "idempotency-key": str(uuid.uuid4()),
        "accept": "text/event-stream",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=== POST /chats/{threadId}/messages ===")
        files = {
            "message": (None, 'Respond with exactly this JSON object and nothing else: {"test": true, "message": "hello"}'),
            "locale": (None, "en"),
            "model": (None, "GPT-4o"),
        }
        url = f"{CHATS_BASE_URL}/chats/{thread_id}/messages"
        print("POST", url)
        r1 = await client.post(url, headers=headers, files=files)
        print("status:", r1.status_code)
        print("body:", r1.text[:2000])

        print()
        print("=== GET /chats/{threadId}/messages (reading it back) ===")
        get_headers = {
            "Authorization": f"Bearer {api_key}",
            "x-source": "external",
            "x-active-org": org_key,
        }
        for attempt in range(6):
            r2 = await client.get(f"{CHATS_BASE_URL}/chats/{thread_id}/messages", headers=get_headers)
            print(f"--- attempt {attempt+1} --- status: {r2.status_code}")
            print(r2.text[:3000])
            if r2.status_code == 200:
                break
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())





