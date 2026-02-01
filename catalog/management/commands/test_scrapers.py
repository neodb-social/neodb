import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.common.downloaders import ScrapDownloader


class Command(BaseCommand):
    help = "Test all configured scraping providers"

    def add_arguments(self, parser):
        parser.add_argument("url", type=str, help="URL to scrape")
        parser.add_argument(
            "--selector",
            type=str,
            default="body",
            help="CSS selector to wait for (default: body)",
        )
        parser.add_argument(
            "--test-string",
            type=str,
            default=None,
            help="String to search for in response to verify success",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=60,
            help="Request timeout in seconds (default: 60)",
        )

    def handle(self, *args, **options):
        url = options["url"]
        selector = options["selector"]
        test_string = options["test_string"]
        timeout = options["timeout"]

        # Get list of configured providers
        providers_str = settings.DOWNLOADER_PROVIDERS
        if not providers_str:
            self.stdout.write(
                self.style.ERROR("No providers configured in DOWNLOADER_PROVIDERS")
            )
            return

        providers = [p.strip() for p in providers_str.split(",") if p.strip()]

        # Add custom provider if configured
        if settings.DOWNLOADER_CUSTOMSCRAPER_URL:
            if "custom" not in providers:
                providers.append("custom")

        if not providers:
            self.stdout.write(self.style.ERROR("No providers configured"))
            return

        self.stdout.write(f"Testing {len(providers)} configured provider(s)...")
        self.stdout.write(f"URL: {url}")
        self.stdout.write(f"Selector: {selector}")
        if test_string:
            self.stdout.write(f"Test string: {test_string}")
        self.stdout.write("-" * 60)

        results = []

        for provider in providers:
            self.stdout.write(f"\n[{provider}] Testing...", ending=" ")
            sys.stdout.flush()

            try:
                downloader = ScrapDownloader(
                    url, timeout=timeout, wait_for_selector=selector
                )
                # Directly call the provider method
                resp, resp_type = downloader._scrape_with_provider(provider)

                if resp_type == 0 and resp is not None:  # RESPONSE_OK
                    if test_string:
                        if test_string in resp.text:
                            self.stdout.write(self.style.SUCCESS("PASS"))
                            results.append((provider, "PASS", None))
                        else:
                            self.stdout.write(
                                self.style.WARNING("FAIL (string not found)")
                            )
                            results.append(
                                (provider, "FAIL", "Test string not found in response")
                            )
                    else:
                        self.stdout.write(self.style.SUCCESS("PASS"))
                        results.append((provider, "PASS", None))
                elif resp_type == -4:  # RESPONSE_QUOTA_EXCEEDED
                    self.stdout.write(self.style.ERROR("FAIL (quota exceeded)"))
                    results.append((provider, "FAIL", "Quota exceeded"))
                else:
                    error_msg = (
                        downloader.logs[-1].get("exception", "Unknown error")
                        if downloader.logs
                        else "Unknown error"
                    )
                    self.stdout.write(self.style.ERROR(f"FAIL ({error_msg})"))
                    results.append((provider, "FAIL", str(error_msg)))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"FAIL ({e})"))
                results.append((provider, "FAIL", str(e)))

        # Print summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Summary:")
        passed = sum(1 for _, status, _ in results if status == "PASS")
        failed = sum(1 for _, status, _ in results if status == "FAIL")

        if passed > 0:
            self.stdout.write(self.style.SUCCESS(f"  PASS: {passed}"))
        if failed > 0:
            self.stdout.write(self.style.ERROR(f"  FAIL: {failed}"))

        if passed == 0:
            self.stdout.write(self.style.ERROR("\nAll providers failed."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"\n{passed} provider(s) working successfully.")
            )
