#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.49"]
# ///
"""
Minimal page rendering server for NEODB_DOWNLOADER_CUSTOMSCRAPER_URL.

  uv run misc/scraper-server.py

then point NeoDB at it:

  NEODB_DOWNLOADER_CUSTOMSCRAPER_URL=http://<host>:8970/scrape?url=__URL__&selector=__SELECTOR__

GET /scrape?url=<page>&selector=<css>[&token=<secret>] answers 200 with the
HTML as rendered by headless Chromium, or an error status with a short text
body. The browser runs as itself, with no attempt to hide automation.

It loads any public URL it is given, so keep it off the internet: bind it to an
address only NeoDB can reach, and set a token when other hosts share that
network. The browser reaches the network only through a built-in proxy that
refuses private addresses, so a page cannot turn it against internal services.
"""

import argparse
import asyncio
import hmac
import ipaddress
import logging
import os
import socket
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import (
    Browser,
    Playwright,
    Response,
    Route,
    async_playwright,
)

logger = logging.getLogger("scraper-server")
SKIPPED_RESOURCES = {"image", "media", "font"}
# Kept as a name: this script also runs on Python older than the repo's, where
# the formatter's unparenthesized `except A, B:` is a syntax error.
LOOKUP_ERRORS = (OSError, ValueError)


class Refused(Exception):
    pass


async def public_addresses(host: str, port: int) -> list[str]:
    """Resolve host and return its addresses, or refuse if any is not public."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        addresses = [str(info[4][0]) for info in infos]
        public = all(ipaddress.ip_address(a).is_global for a in addresses)
    except LOOKUP_ERRORS:
        addresses, public = [], False
    if not addresses or not public:
        raise Refused(f"{host} is not a public address")
    return addresses


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()


class GuardProxy:
    """
    Forward proxy the browser is pinned to. A request interceptor never sees
    redirect hops, so the destination rule is enforced here instead, where every
    connection the browser makes (page, subrequest or redirect) has to pass.
    The proxy resolves the name and connects to the address it checked, which
    also leaves no room for a second, different DNS answer.
    """

    async def connect(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        error: Exception = Refused(f"{host} is not reachable")
        for address in await public_addresses(host, port):
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(address, port), 15
                )
            except OSError as e:  # includes the timeout
                error = e
        raise error

    async def open(
        self, head: bytes
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
        """Connect upstream for a request head; return the reply for the client."""
        request_line, _, rest = head.partition(b"\r\n")
        method, target, version = request_line.decode("latin1").split(" ", 2)
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            reader, writer = await self.connect(host.strip("[]"), int(port))
            return reader, writer, b"HTTP/1.1 200 Connection established\r\n\r\n"
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise Refused("unsupported proxy request")
        reader, writer = await self.connect(parts.hostname, parts.port or 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        # one request per connection keeps every request on a checked host
        headers = [
            line
            for line in rest.split(b"\r\n")
            if line and not line.lower().startswith((b"proxy-", b"connection:"))
        ]
        writer.write(
            f"{method} {path} {version}\r\n".encode("latin1")
            + b"\r\n".join([*headers, b"Connection: close"])
            + b"\r\n\r\n"
        )
        return reader, writer, b""

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            upstream_reader, upstream_writer, reply = await self.open(head)
        except Exception as e:
            if isinstance(e, Refused):
                logger.info("proxy refused: %s", e)
            writer.write(
                b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            writer.close()
            return
        writer.write(reply)
        await asyncio.gather(
            pipe(reader, upstream_writer),
            pipe(upstream_reader, writer),
            return_exceptions=True,
        )


class Server:
    def __init__(self, browser: Browser, args: argparse.Namespace) -> None:
        self.browser = browser
        self.token: str = args.token
        self.timeout: int = args.timeout * 1000
        self.slots = asyncio.Semaphore(args.concurrency)

    @staticmethod
    async def skip_heavy(route: Route) -> None:
        if route.request.resource_type in SKIPPED_RESOURCES:
            await route.abort()
        else:
            await route.continue_()

    async def render(self, url: str, selector: str) -> tuple[int, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Refused("url must be http or https")
        # the proxy enforces this for every connection; here it only gives the
        # caller a clear answer for the page itself
        await public_addresses(parts.hostname, parts.port or 443)

        async with self.slots:
            context = await self.browser.new_context()
            try:
                await context.route("**/*", self.skip_heavy)
                page = await context.new_page()
                statuses: list[int] = []

                def track(response: Response) -> None:
                    # a challenge page can reload into the real one, so the
                    # status that counts is the last main document's
                    request = response.request
                    if (
                        request.is_navigation_request()
                        and request.frame == page.main_frame
                        and not 300 <= response.status < 400
                    ):
                        statuses.append(response.status)

                page.on("response", track)
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout
                )
                if selector:
                    try:
                        await page.wait_for_selector(
                            selector, state="attached", timeout=self.timeout
                        )
                    except Exception:
                        # hand back what loaded; the caller validates the content
                        logger.info("selector %r not found on %s", selector, url)
                status = statuses[-1] if statuses else 502
                return (status if status >= 400 else 200), await page.content()
            finally:
                await context.close()

    async def respond(self, target: str) -> tuple[int, str, str]:
        parts = urlsplit(target)
        query = parse_qs(parts.query)
        if parts.path == "/healthz":
            return 200, "text/plain", "ok"
        if parts.path != "/scrape":
            return 404, "text/plain", "not found"
        if self.token and not hmac.compare_digest(
            query.get("token", [""])[0].encode(), self.token.encode()
        ):
            return 403, "text/plain", "bad token"
        url = query.get("url", [""])[0]
        selector = query.get("selector", [""])[0]
        if selector == "__SELECTOR__":  # NeoDB leaves it when it has no selector
            selector = ""
        try:
            status, body = await self.render(url, selector)
        except Refused as e:
            return 400, "text/plain", str(e)
        except Exception as e:
            logger.warning("render failed for %s: %s", url, e)
            return 502, "text/plain", f"render failed: {type(e).__name__}"
        logger.info("%s %s", status, url)
        return status, "text/html", body

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            method, target, _ = head.split(b"\r\n", 1)[0].decode("latin1").split(" ", 2)
            if method != "GET":
                status, ctype, body = 405, "text/plain", "method not allowed"
            else:
                status, ctype, body = await self.respond(target)
        except Exception:
            status, ctype, body = 400, "text/plain", "bad request"
        data = body.encode("utf-8", errors="replace")
        writer.write(
            f"HTTP/1.1 {status} -\r\nContent-Type: {ctype}; charset=utf-8\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
            + data
        )
        try:
            await writer.drain()
        finally:
            writer.close()


async def launch_browser(playwright: Playwright, proxy_port: int) -> Browser:
    options = {
        "headless": True,
        # <-loopback> removes Chromium's built-in proxy bypass for localhost
        "proxy": {"server": f"http://127.0.0.1:{proxy_port}", "bypass": "<-loopback>"},
        # WebRTC would otherwise send UDP around the proxy
        "args": ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
    }
    try:
        return await playwright.chromium.launch(**options)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        # same interpreter, so the browser matches the installed client
        logger.info("Chromium for this Playwright is missing, downloading it")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )
        return await playwright.chromium.launch(**options)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Page rendering server for NEODB_DOWNLOADER_CUSTOMSCRAPER_URL"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8970)
    parser.add_argument("--timeout", type=int, default=30, help="seconds per step")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--token",
        default=os.environ.get("SCRAPER_TOKEN", ""),
        help="require &token=<value> on each request (or set SCRAPER_TOKEN)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    proxy = await asyncio.start_server(GuardProxy().handle, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    async with proxy, async_playwright() as playwright:
        browser = await launch_browser(playwright, proxy_port)
        server = await asyncio.start_server(
            Server(browser, args).handle, args.host, args.port
        )
        logger.info("listening on %s:%s", args.host, args.port)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
