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
network.
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

from playwright.async_api import Browser, Playwright, Route, async_playwright

logger = logging.getLogger("scraper-server")
SKIPPED_RESOURCES = {"image", "media", "font"}
# Kept as a name: this script also runs on Python older than the repo's, where
# the formatter's unparenthesized `except A, B:` is a syntax error.
LOOKUP_ERRORS = (OSError, ValueError)


class Refused(Exception):
    pass


async def is_public_host(host: str, cache: dict[str, bool]) -> bool:
    if host not in cache:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
            cache[host] = bool(infos) and all(
                ipaddress.ip_address(info[4][0]).is_global for info in infos
            )
        except LOOKUP_ERRORS:
            cache[host] = False
    return cache[host]


async def check_hops(urls: list[str], hosts: dict[str, bool]) -> None:
    for hop in urls:
        host = urlsplit(hop).hostname
        if not host or not await is_public_host(host, hosts):
            raise Refused("page redirected to a non-public address")


class Server:
    def __init__(self, browser: Browser, args: argparse.Namespace) -> None:
        self.browser = browser
        self.token: str = args.token
        self.timeout: int = args.timeout * 1000
        self.slots = asyncio.Semaphore(args.concurrency)

    async def render(self, url: str, selector: str) -> tuple[int, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Refused("url must be http or https")
        hosts: dict[str, bool] = {}
        if not await is_public_host(parts.hostname, hosts):
            raise Refused("url host is not a public address")

        async def guard(route: Route) -> None:
            request = route.request
            target = urlsplit(request.url)
            if target.scheme in ("http", "https") and (
                request.resource_type in SKIPPED_RESOURCES
                or not target.hostname
                or not await is_public_host(target.hostname, hosts)
            ):
                await route.abort()
            else:
                await route.continue_()

        async with self.slots:
            context = await self.browser.new_context()
            try:
                await context.route("**/*", guard)
                page = await context.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout
                )
                status = response.status if response else 502
                # Route handlers never see redirect hops, so they are checked
                # after the fact: a hop to a private address has been requested
                # by then, but its content is not handed back.
                hops = []
                request = response.request if response else None
                while request:
                    hops.append(request.url)
                    request = request.redirected_from
                await check_hops(hops, hosts)
                if selector:
                    try:
                        await page.wait_for_selector(
                            selector, state="attached", timeout=self.timeout
                        )
                    except Exception:
                        # hand back what loaded; the caller validates the content
                        logger.info("selector %r not found on %s", selector, url)
                await check_hops([page.url], hosts)
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


async def launch_browser(playwright: Playwright) -> Browser:
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        # same interpreter, so the browser matches the installed client
        logger.info("Chromium for this Playwright is missing, downloading it")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )
        return await playwright.chromium.launch(headless=True)


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
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright)
        server = await asyncio.start_server(
            Server(browser, args).handle, args.host, args.port
        )
        logger.info("listening on %s:%s", args.host, args.port)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
