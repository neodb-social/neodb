import httpx
from core.files import SSRFAttemptError
from core.json import find_ap_alternate, json_from_response
from core.ld import canonicalise
from users.models.system_actor import SystemActor

from activities.models import Hashtag, Post
from users.models import Domain, Identity, IdentityStates


class SearchService:
    """
    Captures the logic needed to search - reused in the UI and API
    """

    def __init__(self, query: str, identity: Identity | None):
        self.query = query.strip()
        self.identity = identity

    def search_identities_handle(self) -> list[Identity]:
        """
        Searches for identities by their handles
        """

        # Short circuit if it's obviously not for us
        if "://" in self.query:
            return []

        # Try to fetch the user by handle
        handle = self.query.strip("@")
        if not handle:
            return []

        results: set[Identity] = set()
        prio_result: Identity | None = None

        if "@" in handle:
            username, domain = handle.split("@", 1)

            # Resolve the domain to the display domain
            domain_instance = Domain.get_domain(domain)
            if domain_instance is not None:
                prio_result = Identity.objects.filter(
                    domain=domain_instance, username=username
                ).first()
                for identity in Identity.objects.filter(
                    domain=domain_instance,
                    username__iexact=username,
                ):
                    results.add(identity)
            if not results and self.identity is not None:
                # Allow authenticated users to fetch remote
                prio_result = Identity.by_username_and_domain(
                    username, domain_instance or domain, fetch=True
                )
                if prio_result:
                    if prio_result.state == IdentityStates.outdated:
                        prio_result.fetch_actor()

        else:
            prio_result = Identity.objects.filter(local=True, username=handle).first()
            for identity in Identity.objects.filter(username=handle)[:20]:
                results.add(identity)
            for identity in Identity.objects.filter(username__istartswith=handle)[:20]:
                results.add(identity)
            if "." in handle:
                for identity in Identity.objects.filter(domain__domain__iexact=handle)[
                    :20
                ]:
                    results.add(identity)

        if prio_result:
            return [prio_result] + list(results - {prio_result})
        return list(results)

    def search_url(self) -> Post | Identity | None:
        """
        Searches for an identity or post by URL.
        """

        # Short circuit if it's obviously not for us
        if "://" not in self.query:
            return None

        # Fetch the provided URL as the system actor to retrieve the AP JSON.
        # Some hosts (e.g. WordPress's ActivityPub plugin) do not
        # content-negotiate the canonical permalink and always return HTML.
        # When that happens we follow ``Link: rel="alternate"`` (or the
        # equivalent ``<link>`` tag) once to reach the AP object.
        fetcher = self.identity or SystemActor()
        uri = self.query
        json_data: dict | None = None
        seen: set[str] = set()
        for _ in range(2):
            if uri in seen:
                return None
            seen.add(uri)
            try:
                response = fetcher.signed_request(method="get", uri=uri)
            except httpx.RequestError, SSRFAttemptError:
                return None
            if response.status_code >= 400:
                return None
            try:
                json_data = json_from_response(response)
                break
            except ValueError:
                alt = find_ap_alternate(response)
                if not alt:
                    return None
                uri = alt
        if json_data is None:
            return None
        try:
            document = canonicalise(json_data, include_security=True, outbound=False)
        except ValueError:
            return None
        type = document.get("type", "unknown").lower()

        # Is it an identity?
        if type in Identity.ACTOR_TYPES:
            # Try and retrieve the profile by actor URI
            identity = Identity.by_actor_uri(document["id"], create=True)
            if identity and identity.state == IdentityStates.outdated:
                identity.fetch_actor()
            return identity

        # Is it a post?
        elif type in [value.lower() for value in Post.Types.values]:
            # Try and retrieve the post by URI
            # (we do not trust the JSON we just got - fetch from source!)
            try:
                return Post.by_object_uri(
                    document["id"], fetch=True, fetch_as=self.identity
                )
            except Post.DoesNotExist:
                return None

        # Dunno what it is
        else:
            return None

    def search_hashtags(self) -> set[Hashtag]:
        """
        Searches for hashtags by their name
        """

        # Short circuit out if it's obviously not a hashtag
        if "@" in self.query or "://" in self.query:
            return set()

        results: set[Hashtag] = set()
        name = self.query.lstrip("#").lower()
        for hashtag in Hashtag.objects.public().hashtag_or_alias(name)[:10]:
            results.add(hashtag)
        for hashtag in Hashtag.objects.public().filter(hashtag__startswith=name)[:10]:
            results.add(hashtag)
        return results

    def search_post_content(self):
        """
        Searches for posts on an identity via full text search
        """
        return self.identity.posts.unlisted(include_replies=True).filter(
            content__search=self.query
        )[:50]

    def search_all(self):
        """
        Returns all possible results for a search
        """
        results = {
            "identities": self.search_identities_handle(),
            "hashtags": self.search_hashtags(),
            "posts": [],
        }
        url_result = self.search_url()
        if isinstance(url_result, Identity):
            results["identities"].insert(0, url_result)
        if isinstance(url_result, Post):
            results["posts"] = [url_result]
        return results
