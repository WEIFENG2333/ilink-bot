"""Bot with slash-command handling and filter composition.

Demonstrates:
- Command filters (/help, /ping)
- Keyword matching
- Filter combination with & | ~
- Error handling
"""

from ilink_bot import WeChatBot, filters

bot = WeChatBot()


@bot.on_message(filters.command("help"))
async def help_handler(msg):
    await msg.reply(
        "Available commands:\n"
        "/help  — Show this help\n"
        "/ping  — Check if bot is alive\n"
        "/echo <text> — Echo back your text"
    )


@bot.on_message(filters.command("ping"))
async def ping_handler(msg):
    await msg.reply("Pong!")


@bot.on_message(filters.command("echo"))
async def echo_handler(msg):
    text = (msg.text or "").removeprefix("/echo").strip()
    if text:
        await msg.reply(text)
    else:
        await msg.reply("Usage: /echo <text>")


@bot.on_message(filters.text & filters.contains("hello"))
async def greet(msg):
    await msg.reply("Hello! How can I help you?")


@bot.on_message(filters.text & ~filters.contains("/"))
async def fallback(msg):
    await msg.reply("I don't understand. Try /help for available commands.")


@bot.on_error
async def error_handler(exc, msg):
    print(f"Error: {exc}")
    if msg:
        await msg.reply("Sorry, something went wrong.")


if __name__ == "__main__":
    bot.run()
