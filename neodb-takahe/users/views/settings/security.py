from django import forms
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import FormView


@method_decorator(login_required, name="dispatch")
class SecurityPage(FormView):
    """
    Lets the identity's profile be edited
    """

    template_name = "settings/login_security.html"
    extra_context = {"section": "security"}

    class form_class(forms.Form):
        email = forms.EmailField(
            disabled=True,
            help_text="Your email address cannot be changed yet.",
        )

    def get_initial(self):
        return {"email": self.request.user.email}

    def get_context_data(self, **kwargs):
        kwargs["identities"] = self.request.user.identities.all()
        return super().get_context_data(**kwargs)
