from captcha.fields import CaptchaField
from django import forms


class EmailLoginForm(forms.Form):
    human = CaptchaField()
    email = forms.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["human"].widget.attrs.update(
            {
                "placeholder": "CAPTCHA",
                "class": "code-input",
            }
        )
