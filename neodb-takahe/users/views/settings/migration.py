from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.generic import FormView

from users.models import Identity
from users.models.identity import IdentityStates
from users.views.base import IdentityViewMixin


@method_decorator(login_required, name="dispatch")
class MigrateInPage(IdentityViewMixin, FormView):
    """
    Lets the identity's profile be migrated in or out.
    """

    template_name = "settings/migrate_in.html"
    extra_context = {"section": "migrate_in"}

    class form_class(forms.Form):
        alias = forms.CharField(
            help_text="The @account@example.com username you want to move here"
        )

        def clean_alias(self):
            self.alias_identity = Identity.by_handle(
                self.cleaned_data["alias"], fetch=True
            )
            if self.alias_identity is None:
                raise forms.ValidationError("Cannot find that account.")
            return self.alias_identity.actor_uri

    def form_valid(self, form):
        if self.identity.has_moved():
            messages.error(
                self.request, "Alias update not allowed for a moved account."
            )
            return redirect(".")
        if "alias" not in form.cleaned_data:
            messages.error(self.request, "No alias specified.")
            return redirect(".")

        if "remove_alias" in self.request.GET:
            self.identity.remove_alias(form.cleaned_data["alias"])
            messages.info(
                self.request, f"Alias to {form.alias_identity.handle} removed"
            )
        else:
            self.identity.add_alias(form.cleaned_data["alias"])
            messages.info(self.request, f"Alias to {form.alias_identity.handle} added")
        return redirect(".")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["moved"] = self.identity.has_moved()
        context["aliases"] = []
        if self.identity.aliases:
            context["aliases"] = [
                Identity.by_actor_uri(uri) for uri in self.identity.aliases
            ]
        return context


@method_decorator(login_required, name="dispatch")
class MigrateOutPage(IdentityViewMixin, FormView):
    """
    Lets the identity's profile be migrated in or out.
    """

    template_name = "settings/migrate_out.html"
    extra_context = {"section": "migrate_out"}

    class form_class(forms.Form):
        alias = forms.CharField(
            help_text="The @account@example.com username you would like to move to",
            required=False,
        )

        def clean_alias(self):
            self.alias_identity = Identity.by_handle(
                self.cleaned_data["alias"], fetch=True
            )
            if self.alias_identity is None:
                raise forms.ValidationError("Cannot find that account.")
            if self.alias_identity.local:
                raise forms.ValidationError("Cannot migrate to a local account.")
            self.alias_identity.fetch_actor()
            return self.alias_identity.actor_uri

    def form_valid(self, form):
        if "cancel" in self.request.GET:
            self.identity.aliases = []
            self.identity.save(update_fields=["aliases"])
            self.identity.transition_perform(IdentityStates.updated)
            messages.info(self.request, "Migration cancelled.")
        elif form.cleaned_data.get("alias"):
            if self.identity.actor_uri not in (form.alias_identity.aliases or []):
                messages.error(
                    self.request, "You must set up an alias in target account first."
                )
                return redirect(".")
            self.identity.aliases = [form.cleaned_data["alias"]]
            self.identity.save(update_fields=["aliases"])
            self.identity.transition_perform(IdentityStates.moved)
            messages.info(self.request, f"Start moving to {form.alias_identity.handle}")
        return redirect(".")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["moved"] = self.identity.has_moved()
        context["aliases"] = []
        if self.identity.aliases:
            context["aliases"] = [
                Identity.by_actor_uri(uri) for uri in self.identity.aliases
            ]
        return context
