from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import ValidationError

from .constants import STUDY_DIRECTIONS
from .models import ApplicantProfile, User
from .services import totp as totp_service


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Электронная почта",
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
    )
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        strip=False,
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class RegistrationForm(forms.Form):
    """Applicant self registration. The role is always ``applicant`` here."""

    email = forms.EmailField(label="Электронная почта (login)", max_length=254)
    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
        help_text="Минимум 10 символов, не только цифры, не из списка популярных.",
    )
    password2 = forms.CharField(
        label="Пароль ещё раз", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
    )
    last_name = forms.CharField(label="Фамилия", max_length=80)
    first_name = forms.CharField(label="Имя", max_length=80)
    middle_name = forms.CharField(label="Отчество", max_length=80, required=False)
    birth_date = forms.DateField(
        label="Дата рождения",
        widget=forms.DateInput(attrs={"type": "date"}),
        input_formats=("%Y-%m-%d", "%d.%m.%Y"),
    )
    direction = forms.ChoiceField(
        label="Направление подготовки", choices=[("", "— выберите —")] + STUDY_DIRECTIONS
    )
    company = forms.CharField(
        label="Организация (не заполнять)",
        required=False,
        widget=forms.HiddenInput,
        help_text="Скрытое поле-приманка против спам-ботов.",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Пользователь с такой почтой уже зарегистрирован.")
        return email

    def clean_company(self):
        # A real applicant never sees this field rendered as an input.
        value = self.cleaned_data.get("company")
        if value:
            raise ValidationError("Регистрация отклонена.")
        return ""

    def clean_birth_date(self):
        from django.utils import timezone

        value = self.cleaned_data["birth_date"]
        today = timezone.localdate()
        if value > today:
            raise ValidationError("Дата рождения не может быть в будущем.")
        if value.year > today.year - 8:
            raise ValidationError("Слишком юный абитуриент.")
        return value

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("Пароли не совпадают.")
        if p1:
            password_validation.validate_password(p1)
        return p1

    def save(self):
        user = User.objects.create_user(
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
        )
        ApplicantProfile.objects.create(
            user=user,
            last_name=self.cleaned_data["last_name"].strip(),
            first_name=self.cleaned_data["first_name"].strip(),
            middle_name=self.cleaned_data["middle_name"].strip(),
            birth_date=self.cleaned_data["birth_date"],
            direction=self.cleaned_data["direction"],
        )
        return user


class TotpCodeForm(forms.Form):
    """Used both for enrolment and for the login challenge."""

    code = forms.CharField(
        label="Код из приложения",
        min_length=6,
        max_length=8,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                      "placeholder": "123456", "autofocus": True}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.accepted_step = None

    def clean_code(self):
        code = self.cleaned_data["code"].strip().replace(" ", "").replace("-", "")
        if not code.isdigit():
            raise forms.ValidationError("Код состоит только из цифр.")
        return code


class RecoveryCodeForm(forms.Form):
    code = forms.CharField(
        label="Резервный код",
        max_length=32,
        widget=forms.TextInput(attrs={"placeholder": "xxxx-xxxx-xxxx", "autofocus": True}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_code(self):
        code = self.cleaned_data["code"].strip().lower().replace(" ", "")
        if not totp_service.check_recovery_code(self.user, code):
            raise forms.ValidationError("Резервный код недействителен или уже использован.")
        return code


class MaxCodeForm(forms.Form):
    code = forms.CharField(
        label="Код из чата MAX",
        min_length=6,
        max_length=8,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                      "placeholder": "123456", "autofocus": True}),
    )

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Код состоит только из цифр.")
        return code


class ApplicantProfileForm(forms.ModelForm):
    class Meta:
        model = ApplicantProfile
        fields = [
            "last_name", "first_name", "middle_name", "birth_date", "phone", "region",
            "school", "direction", "exam_russian", "exam_math", "exam_info", "exam_physics",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "phone": forms.TextInput(attrs={"placeholder": "+7 301 000-00-00"}),
        }

    def clean(self):
        cleaned = super().clean()
        for field in ("exam_russian", "exam_math", "exam_info", "exam_physics"):
            value = cleaned.get(field)
            if value is not None and not 0 <= value <= 100:
                self.add_error(field, "Балл ЕГЭ — от 0 до 100.")
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=False)
        if profile.status == "draft" and profile.is_complete:
            profile.status = "draft"
        if commit:
            profile.save()
        return profile


class ChangePasswordForm(SetPasswordForm):
    """Password change for a logged in user (old password is required)."""

    old_password = forms.CharField(
        label="Текущий пароль",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        strip=False,
    )

    def clean_old_password(self):
        old = self.cleaned_data.get("old_password", "")
        if not self.user.check_password(old):
            raise ValidationError("Текущий пароль указан неверно.")
        return old

    def save(self, commit=True):
        user = super().save(commit=commit)
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(self.user, user)
        return user
