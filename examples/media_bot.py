"""Media bot — demonstrates image / file / video handling.

Usage:
    1. Run `ilink-bot login` first to authenticate
    2. Run `python examples/media_bot.py`
    3. Send an image or file to the bot from your WeChat
"""

from ilink_bot import WeChatBot, filters

bot = WeChatBot()


@bot.on_message(filters.image)
async def handle_image(msg):
    """Download a received image and reply with a confirmation."""
    image_item = msg.image
    if image_item and image_item.media and image_item.media.encrypt_query_param:
        # Download the image via CDN
        data = await bot.client.download_media(
            image_item.media.encrypt_query_param,
            image_item.media.aes_key or "",
        )
        await msg.reply(f"Received your image! ({len(data)} bytes)")
    else:
        await msg.reply("Received an image (no media info available)")


@bot.on_message(filters.file)
async def handle_file(msg):
    """Acknowledge a received file."""
    file_item = msg.file
    name = file_item.file_name if file_item else "unknown"
    await msg.reply(f"Got your file: {name}")


@bot.on_message(filters.text & filters.contains("send image"))
async def send_test_image(msg):
    """Send a small test PNG image when the user says 'send image'."""
    # Create a minimal 1x1 red PNG (67 bytes)
    import base64

    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
        "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
    )
    await msg.reply_image(tiny_png)


@bot.on_message(filters.text)
async def echo(msg):
    await msg.reply(f"Echo: {msg.text}")


if __name__ == "__main__":
    bot.run()
