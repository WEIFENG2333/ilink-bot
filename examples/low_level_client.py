"""Low-level client usage — direct API access without the bot framework.

Useful for scripts, one-off sends, or custom polling implementations.
"""

import asyncio

from ilink_bot import ILinkClient


async def main():
    # Uses token from ~/.ilink-bot/token.json or ILINK_TOKEN env var
    async with ILinkClient() as client:
        # Check connection
        info = client.get_bot_info()
        print(f"Connected: {info.connected}")
        print(f"Bot ID: {info.bot_id}")

        # Send a message (requires a known user ID)
        # result = await client.send_text("user@im.wechat", "Hello from Python!")
        # print(f"Sent: {result}")

        # Poll for messages (single iteration)
        resp = await client.get_updates()
        for msg in resp.msgs:
            text = ""
            for item in msg.item_list or []:
                if item.type == 1 and item.text_item:
                    text = item.text_item.text or ""
            print(f"From: {msg.from_user_id} — {text}")


if __name__ == "__main__":
    asyncio.run(main())
