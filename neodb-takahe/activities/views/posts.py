from core.decorators import cache_page_by_ap_json
from core.ld import canonicalise
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.vary import vary_on_headers
from django.views.generic import TemplateView
from users.shortcuts import by_handle_or_404

from activities.models import Post, PostStates, QuoteAuthorization
from activities.services import PostService
from users.models import Identity


@method_decorator(
    cache_page_by_ap_json("cache_timeout_page_post", public_only=True), name="dispatch"
)
@method_decorator(vary_on_headers("Accept"), name="dispatch")
class Individual(TemplateView):
    template_name = "activities/post.html"

    identity: Identity
    post_obj: Post

    def get(self, request, handle, post_id):
        self.identity = by_handle_or_404(self.request, handle, local=False)
        if self.identity.blocked:
            raise Http404("Blocked user")
        self.post_obj = get_object_or_404(
            PostService.queryset()
            .filter(author=self.identity)
            .unlisted(include_replies=True),
            pk=post_id,
        )
        if self.post_obj.state in [PostStates.deleted, PostStates.deleted_fanned_out]:
            raise Http404("Deleted post")
        # If they're coming in looking for JSON, they want the actor
        if request.ap_json:
            # Return post JSON
            return self.serve_object()
        else:
            # Show normal page
            return super().get(request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        ancestors, descendants = PostService(self.post_obj).context(
            identity=None, num_ancestors=2
        )

        context.update(
            {
                "identity": self.identity,
                "post": self.post_obj,
                "link_original": True,
                "ancestors": ancestors,
                "descendants": descendants,
            }
        )

        return context

    def serve_object(self):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        # If this not a local post, redirect to its canonical URI
        if not self.post_obj.local:
            return redirect(self.post_obj.object_uri)
        return JsonResponse(
            canonicalise(self.post_obj.to_ap(), include_security=True),
            content_type="application/activity+json",
        )


class PostRepliesCollection(View):
    """
    ActivityPub replies collection for a post.
    Returns public/unlisted replies as an AP Collection.
    """

    REPLIES_LIMIT = 50

    def get(self, request, handle, post_id):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        identity = by_handle_or_404(request, handle, local=False)
        if not identity.local:
            raise Http404("Not a local identity")
        post_obj = get_object_or_404(
            Post.objects.filter(author=identity),
            pk=post_id,
        )
        if not post_obj.local:
            raise Http404("Not a local post")
        if post_obj.state in [PostStates.deleted, PostStates.deleted_fanned_out]:
            raise Http404("Deleted post")

        replies_uri = post_obj.object_uri + "replies/"
        reply_uris = list(
            Post.objects.filter(
                in_reply_to=post_obj.object_uri,
                visibility__in=[
                    Post.Visibilities.public,
                    Post.Visibilities.unlisted,
                ],
            )
            .not_hidden()
            .order_by("published")
            .values_list("object_uri", flat=True)[: self.REPLIES_LIMIT]
        )
        collection = {
            "id": replies_uri,
            "type": "Collection",
            "totalItems": len(reply_uris),
            "first": {
                "type": "CollectionPage",
                "partOf": replies_uri,
                "items": reply_uris,
            },
        }
        return JsonResponse(
            canonicalise(collection),
            content_type="application/activity+json",
        )


class QuoteAuthorizationView(View):
    """
    Serves a FEP-044f QuoteAuthorization at a dereferenceable URL so that
    third-party servers can verify quotes of local posts.
    """

    def get(self, request, handle, post_id, auth_id):
        if settings.SETUP.NO_FEDERATION:
            return HttpResponse(status=503)
        identity = by_handle_or_404(request, handle, local=False)
        if not identity.local:
            raise Http404("Not a local identity")
        auth = get_object_or_404(
            QuoteAuthorization.objects.select_related(
                "target_post", "target_post__author"
            ),
            pk=auth_id,
            target_post__pk=post_id,
            target_post__author=identity,
        )
        if not auth.target_post.local:
            raise Http404("Not a local post")
        if auth.target_post.state in [
            PostStates.deleted,
            PostStates.deleted_fanned_out,
        ]:
            raise Http404("Deleted post")
        return JsonResponse(
            canonicalise(auth.to_ap(), include_security=True),
            content_type="application/activity+json",
        )
