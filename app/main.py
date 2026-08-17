from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import socket
import time
import urllib.request
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

CLOAK_CDP_URL = os.getenv(
    "CLOAK_CDP_URL",
    "http://cloakbrowser:9222",
).rstrip("/")

ADAPTER_API_KEY = os.getenv("CLOAK_ADAPTER_API_KEY", "")

MAX_TIMEOUT_MS = min(
    max(int(os.getenv("CLOAK_MAX_TIMEOUT_MS", "120000")), 10000),
    180000,
)

WAIT_AFTER_LOAD_SECONDS = min(
    max(float(os.getenv("CLOAK_WAIT_AFTER_LOAD_SECONDS", "5")), 0),
    30,
)

browser_lock = asyncio.Lock()

app = FastAPI(
    title="CloakBrowser Prowlarr Adapter",
    docs_url=None,
    redoc_url=None,
)


class URLPolicyError(ValueError):
    pass


class URLResolutionError(ValueError):
    pass


def parse_network_url(value: str, *, navigation: bool) -> tuple[str, int]:
    parsed = urlparse(value)

    allowed_schemes = {"http", "https"} if navigation else {
        "http",
        "https",
        "ws",
        "wss",
    }

    if parsed.scheme not in allowed_schemes:
        raise URLPolicyError(
            "Only public HTTP(S) destinations are supported"
        )

    if parsed.username or parsed.password:
        raise URLPolicyError("Credentials in URLs are forbidden")

    if not parsed.hostname:
        raise URLPolicyError("URL hostname is required")

    default_port = 443 if parsed.scheme in {"https", "wss"} else 80
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise URLPolicyError("Invalid URL port") from exc

    return parsed.hostname.rstrip("."), port


async def resolve_public_addresses(hostname: str, port: int) -> list[str]:
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise URLResolutionError(
                f"Name does not resolve: {hostname}"
            ) from exc

        addresses = []
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if address not in addresses:
                addresses.append(address)

    if not addresses:
        raise URLResolutionError(f"Name does not resolve: {hostname}")

    forbidden = [str(address) for address in addresses if not address.is_global]
    if forbidden:
        raise URLPolicyError(
            f"Destination resolves to a forbidden address: {hostname}"
        )

    return [str(address) for address in addresses]


async def validate_public_url(value: str, *, navigation: bool = True) -> str:
    hostname, port = parse_network_url(value, navigation=navigation)
    await resolve_public_addresses(hostname, port)

    return value


def authorized(request: Request) -> bool:
    if not ADAPTER_API_KEY:
        return True

    supplied = request.headers.get("X-Cloak-Api-Key", "")
    return hmac.compare_digest(supplied, ADAPTER_API_KEY)


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def error_response(message: str, started: int, status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        {
            "status": "error",
            "message": message,
            "startTimestamp": started,
            "endTimestamp": timestamp_ms(),
            "version": "cloak-prowlarr-0.2.0",
        },
        status_code=status_code,
    )


@app.get("/")
async def root() -> dict:
    return {
        "msg": "CloakBrowser Prowlarr adapter is ready!",
        "version": "0.2.0",
        "target_policy": "any-public-http-https",
    }


@app.get("/health")
async def health() -> JSONResponse:
    def check_cdp() -> dict:
        with urllib.request.urlopen(
            f"{CLOAK_CDP_URL}/json/version",
            timeout=5,
        ) as response:
            return json.load(response)

    try:
        version = await asyncio.to_thread(check_cdp)
        return JSONResponse(
            {
                "ok": True,
                "browser": version.get("Browser"),
                "cdp": True,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "cdp": False,
                "error": type(exc).__name__,
            },
            status_code=503,
        )


@app.post("/v1")
async def flaresolverr_compatible(request: Request) -> JSONResponse:
    started = timestamp_ms()

    if not authorized(request):
        return error_response("Unauthorized", started, 401)

    try:
        payload = await request.json()
    except Exception:
        return error_response("Invalid JSON body", started, 400)

    command = str(payload.get("cmd", ""))

    if command != "request.get":
        return error_response(
            f"Unsupported command: {command}",
            started,
            400,
        )

    try:
        target_url = await validate_public_url(str(payload.get("url", "")))
    except URLResolutionError as exc:
        return error_response(str(exc), started, 502)
    except URLPolicyError as exc:
        return error_response(str(exc), started, 403)

    requested_timeout = int(payload.get("maxTimeout") or MAX_TIMEOUT_MS)
    timeout_ms = min(max(requested_timeout, 10000), MAX_TIMEOUT_MS)

    async with browser_lock:
        page = None
        blocked_requests: list[str] = []
        blocked_navigation: list[Exception] = []

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    CLOAK_CDP_URL,
                    timeout=10000,
                )

                if not browser.contexts:
                    return error_response(
                        "CloakBrowser has no browser context",
                        started,
                    )

                context = browser.contexts[0]
                page = await context.new_page()

                async def route_request(route, browser_request):
                    parsed = urlparse(browser_request.url)

                    if parsed.scheme in {"data", "blob", "about"}:
                        await route.continue_()
                        return

                    try:
                        await validate_public_url(
                            browser_request.url,
                            navigation=False,
                        )
                    except (URLPolicyError, URLResolutionError) as exc:
                        blocked_requests.append(str(exc))
                        if browser_request.is_navigation_request():
                            blocked_navigation.append(exc)
                        await route.abort("blockedbyclient")
                        return

                    await route.continue_()

                await page.route("**/*", route_request)

                navigation = await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )

                await asyncio.sleep(WAIT_AFTER_LOAD_SECONDS)

                final_url = page.url
                await validate_public_url(final_url)

                html = await page.content()
                user_agent = await page.evaluate("navigator.userAgent")
                cookies = await context.cookies([final_url])

                status = navigation.status if navigation else 200
                headers = (
                    await navigation.all_headers()
                    if navigation is not None
                    else {}
                )

                solution = {
                    "url": final_url,
                    "status": status,
                    "headers": headers,
                    "response": html,
                    "cookies": cookies,
                    "userAgent": user_agent,
                    "blockedRequests": len(blocked_requests),
                }

                return JSONResponse(
                    {
                        "status": "ok",
                        "message": "",
                        "solution": solution,
                        "startTimestamp": started,
                        "endTimestamp": timestamp_ms(),
                        "version": "cloak-prowlarr-0.2.0",
                    }
                )

        except Exception as exc:
            if blocked_navigation:
                blocked = blocked_navigation[0]
                status_code = (
                    502 if isinstance(blocked, URLResolutionError) else 403
                )
                return error_response(str(blocked), started, status_code)
            return error_response(
                f"{type(exc).__name__}: {exc}",
                started,
            )

        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
