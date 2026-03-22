"""Minimal echo bot — replies with whatever the user sends.

Usage:
    1. Run `ilink-bot login` first to authenticate
    2. Run `python examples/echo_bot.py`
    3. Send a message to the bot from your WeChat
"""

from ilink_bot import WeChatBot, filters

bot = WeChatBot()


@bot.on_message(filters.text)
async def echo(msg):
    await msg.reply(f"You said: {msg.text}")


@bot.on_message(filters.image)
async def handle_image(msg):
    await msg.reply("Received an image!")


@bot.on_message(filters.voice)
async def handle_voice(msg):
    text = msg.text  # voice-to-text if available
    if text:
        await msg.reply(f"Voice transcription: {text}")
    else:
        await msg.reply("Received a voice message!")


if __name__ == "__main__":
    bot.run()
