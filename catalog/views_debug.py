"""
Debug views for testing scrapers.
Access: DEBUG=True allows anyone, otherwise requires superuser.
"""

import json
import time
import traceback

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .common import (
    BasicDownloader,
    ProxiedDownloader,
    RetryDownloader,
    ScrapDownloader,
    SiteManager,
)


def _check_access(request):
    """Check if user has access to debug views."""
    if settings.DEBUG:
        return True
    return request.user.is_authenticated and request.user.is_superuser


def scraper_debug_page(request):
    """Serve the scraper debug HTML page."""
    if not _check_access(request):
        raise Http404("Not found")
    return render(request, "scraper_debug.html")


@require_http_methods(["POST"])
def scraper_debug_api(request):
    """API endpoint to run scraper and return results."""
    if not _check_access(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body)
        url = data.get("url", "")
        mode = data.get("mode", "site")  # site, downloader, provider
        downloader_type = data.get("downloader", "basic")
        provider = data.get("provider", "")
        selector = data.get("selector", "")
        timeout = data.get("timeout", 90)

        logs = []
        logs.append(f"URL: {url}")
        logs.append(f"Mode: {mode}")

        html_content = ""
        metadata = {}
        start = time.time()

        if mode == "site":
            # Mode 1: Use normal site logic
            site = SiteManager.get_site_by_url(url)
            if not site:
                return JsonResponse(
                    {"error": f"No site handler found for URL: {url}"}, status=400
                )

            logs.append(f"Site: {site.__class__.__name__}")
            logs.append("Scraping with site parser...")

            try:
                resource = site.scrape()
                elapsed = time.time() - start
                logs.append(f"Scrape completed in {elapsed:.2f}s")

                if resource:
                    metadata = resource.metadata
                    logs.append(f"Got {len(metadata)} metadata fields")
                else:
                    logs.append("No resource returned")

            except Exception as e:
                logs.append(f"Scrape error: {e}")
                logs.append(traceback.format_exc())
                return JsonResponse(
                    {
                        "error": str(e),
                        "logs": logs,
                        "traceback": traceback.format_exc(),
                    },
                    status=500,
                )

        elif mode == "downloader":
            # Mode 2: Test different downloader types
            logs.append(f"Downloader: {downloader_type}")

            if downloader_type == "scrap":
                dl = ScrapDownloader(
                    url, timeout=timeout, wait_for_selector=selector or None
                )
                logs.append(f"Selector: {selector or '(none)'}")
            elif downloader_type == "proxied":
                dl = ProxiedDownloader(url, timeout=timeout)
            elif downloader_type == "retry":
                dl = RetryDownloader(url, timeout=timeout)
            else:
                dl = BasicDownloader(url, timeout=timeout)

            try:
                resp = dl.download()
                elapsed = time.time() - start
                logs.append(f"Download completed in {elapsed:.2f}s")
                logs.append(f"Response status: {resp.status_code}")
                logs.append(f"Response type: {type(resp).__name__}")

                html_content = resp.text
                if len(html_content) > 100000:
                    logs.append(
                        f"HTML truncated from {len(html_content)} to 100000 chars"
                    )
                    html_content = html_content[:100000]

                # Add downloader logs
                for log in dl.logs:
                    logs.append(f"DL: {log}")

            except Exception as e:
                logs.append(f"Download error: {e}")
                for log in getattr(dl, "logs", []):
                    logs.append(f"DL: {log}")
                return JsonResponse(
                    {
                        "error": str(e),
                        "logs": logs,
                        "traceback": traceback.format_exc(),
                    },
                    status=500,
                )

        elif mode == "provider":
            # Mode 3: Test specific ScrapDownloader provider
            logs.append(f"Provider: {provider}")
            logs.append(f"Selector: {selector or '(none)'}")

            dl = ScrapDownloader(
                url, timeout=timeout, wait_for_selector=selector or None
            )

            try:
                resp, resp_type = dl._scrape_with_provider(provider)
                elapsed = time.time() - start
                logs.append(f"Provider call completed in {elapsed:.2f}s")
                logs.append(f"Response type code: {resp_type}")

                # Map response type to name
                resp_type_names = {
                    0: "RESPONSE_OK",
                    -1: "RESPONSE_INVALID_CONTENT",
                    -2: "RESPONSE_NETWORK_ERROR",
                    -3: "RESPONSE_CENSORSHIP",
                    -4: "RESPONSE_QUOTA_EXCEEDED",
                }
                logs.append(
                    f"Response type: {resp_type_names.get(resp_type, 'UNKNOWN')}"
                )

                if resp:
                    logs.append(f"Response class: {type(resp).__name__}")
                    logs.append(f"Response status: {resp.status_code}")
                    html_content = resp.text
                    if len(html_content) > 100000:
                        logs.append(
                            f"HTML truncated from {len(html_content)} to 100000 chars"
                        )
                        html_content = html_content[:100000]
                else:
                    logs.append("No response object returned")

                # Add downloader logs
                for log in dl.logs:
                    logs.append(f"DL: {log}")

            except Exception as e:
                logs.append(f"Provider error: {e}")
                for log in getattr(dl, "logs", []):
                    logs.append(f"DL: {log}")
                return JsonResponse(
                    {
                        "error": str(e),
                        "logs": logs,
                        "traceback": traceback.format_exc(),
                    },
                    status=500,
                )

        else:
            return JsonResponse({"error": f"Unknown mode: {mode}"}, status=400)

        elapsed = time.time() - start
        logs.append(f"Total time: {elapsed:.2f}s")

        return JsonResponse(
            {
                "success": True,
                "html": html_content,
                "metadata": metadata,
                "logs": logs,
                "elapsed": round(elapsed, 2),
            }
        )

    except Exception as e:
        return JsonResponse(
            {"error": str(e), "traceback": traceback.format_exc()}, status=500
        )
