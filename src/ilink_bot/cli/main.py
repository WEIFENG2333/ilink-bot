"""CLI entry point for ilink-bot.

Usage::

    ilink-bot login           # QR-code login
    ilink-bot send "message"  # Send a message
    ilink-bot status          # Check bot status
    ilink-bot mcp             # Start MCP server
    ilink-bot webhook         # Start webhook gateway
"""

from __future__ import annotations

import asyncio
import logging
import sys

try:
    import typer
    from rich.console import Console
    from rich.logging import RichHandler
except ImportError:
    print(
        "CLI dependencies not installed. Run: pip install ilink-bot[cli]",
        file=sys.stderr,
    )
    sys.exit(1)

from typing import TYPE_CHECKING

from ilink_bot.client.client import DEFAULT_TOKEN_FILE, ILinkClient

if TYPE_CHECKING:
    from pathlib import Path

app = typer.Typer(
    name="ilink-bot",
    help="Standalone CLI for WeChat iLink Bot protocol",
    add_completion=False,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_time=False)],
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def login(
    token_file: Path = typer.Option(
        DEFAULT_TOKEN_FILE, "--token-file", "-f", help="Token file path"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scan QR code to login with your WeChat account."""
    _setup_logging(verbose)

    async def _login() -> None:
        async with ILinkClient(token_file=token_file) as client:
            console.print("[bold]Fetching QR code...[/bold]")
            qr = await client.get_qrcode()
            console.print("\n[cyan]Scan this QR code with WeChat:[/cyan]")
            console.print(f"[link={qr.qrcode_img_content}]{qr.qrcode_img_content}[/link]\n")

            # Try to display QR in terminal
            try:
                import qrcode as qr_lib  # type: ignore[import-not-found,unused-ignore]

                q = qr_lib.QRCode(border=1)
                q.add_data(qr.qrcode_img_content)
                q.print_ascii(invert=True)
            except ImportError:
                console.print("[dim](Install 'qrcode' package for terminal QR display)[/dim]")

            console.print("[yellow]Waiting for scan...[/yellow]")
            result = await client.login()
            console.print("\n[bold green]Login successful![/bold green]")
            console.print(f"  Bot ID:  {result.bot_id}")
            console.print(f"  Token saved to: {token_file}")

    asyncio.run(_login())


@app.command()
def send(
    message: str = typer.Argument(..., help="Message text to send"),
    to: str = typer.Option("", "--to", "-t", help="Recipient user ID"),
    token: str = typer.Option(
        "", "--token", "-k", help="Bot token (skip login)", envvar="ILINK_TOKEN"
    ),
    token_file: Path = typer.Option(DEFAULT_TOKEN_FILE, "--token-file", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Send a text message via WeChat."""
    _setup_logging(verbose)

    # Support piped input
    if message == "-":
        message = sys.stdin.read().strip()

    if not message:
        console.print("[red]Error: empty message[/red]")
        raise typer.Exit(1)

    async def _send() -> None:
        async with ILinkClient(token=token or None, token_file=token_file) as client:
            if not client.is_authenticated:
                console.print("[red]Not logged in. Run `ilink-bot login` first.[/red]")
                raise typer.Exit(1)
            if not to:
                console.print("[red]Recipient required. Use --to <user_id>[/red]")
                raise typer.Exit(1)
            result = await client.send_text(to, message)
            console.print(f"[green]Sent![/green] (id={result.get('message_id', 'unknown')})")

    asyncio.run(_send())


@app.command()
def status(
    token: str = typer.Option("", "--token", "-k", help="Bot token", envvar="ILINK_TOKEN"),
    token_file: Path = typer.Option(DEFAULT_TOKEN_FILE, "--token-file", "-f"),
) -> None:
    """Show current bot connection status."""
    client = ILinkClient(token=token or None, token_file=token_file)
    info = client.get_bot_info()
    if info.connected:
        console.print("[bold green]Connected[/bold green]")
        console.print(f"  Bot ID:   {info.bot_id}")
        console.print(f"  User ID:  {info.user_id}")
        console.print(f"  Base URL: {info.base_url}")
    else:
        console.print("[bold red]Not connected[/bold red]")
        console.print("Run `ilink-bot login` to authenticate.")


@app.command()
def mcp(
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio or http"),
    port: int = typer.Option(8080, "--port", "-p", help="HTTP port (only for http transport)"),
    token_file: Path = typer.Option(DEFAULT_TOKEN_FILE, "--token-file", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the MCP server for AI tool integration."""
    _setup_logging(verbose)

    try:
        from ilink_bot.mcp.server import run_mcp_server
    except ImportError:
        console.print("[red]MCP support not installed. Run: pip install ilink-bot[mcp][/red]")
        raise typer.Exit(1) from None

    # Read token from file
    client = ILinkClient(token_file=token_file)
    run_mcp_server(
        transport=transport,
        port=port,
        token=client.token,
        token_file=str(token_file),
    )


@app.command()
def webhook(
    url: str = typer.Option(..., "--url", "-u", help="Webhook URL to push messages to"),
    secret: str = typer.Option("", "--secret", "-s", help="HMAC-SHA256 signing secret"),
    token: str = typer.Option(
        "", "--token", "-k", help="Bot token (skip login)", envvar="ILINK_TOKEN"
    ),
    token_file: Path = typer.Option(DEFAULT_TOKEN_FILE, "--token-file", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the webhook gateway (long-poll → HTTP POST)."""
    _setup_logging(verbose)

    from ilink_bot.webhook.gateway import WebhookConfig, WebhookGateway

    async def _run() -> None:
        client = ILinkClient(token=token or None, token_file=token_file)
        if not client.is_authenticated:
            console.print("[red]Not logged in. Run `ilink-bot login` first.[/red]")
            raise typer.Exit(1)

        config = WebhookConfig(url=url, secret=secret)
        gateway = WebhookGateway(client=client, config=config)
        console.print(f"[green]Webhook gateway starting → {url}[/green]")
        await gateway.run()

    asyncio.run(_run())


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
