from django import forms
from django.core.validators import MaxValueValidator

from accounts.constants import STUDY_DIRECTIONS

from .models import Enrollment, Event


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ["title", "event_type", "description", "start_at", "end_at", "location",
                  "capacity", "bonus_points", "status"]
        widgets = {
            "start_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("start_at", "end_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
            self.fields[name].widget.format = "%Y-%m-%dT%H:%M"
        self.fields["end_at"].required = False

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_at"), cleaned.get("end_at")
        if start and end and end <= start:
            self.add_error("end_at", "Окончание должно быть позже начала.")
        return cleaned


class EnrollmentNoteForm(forms.Form):
    note = forms.CharField(
        label="Комментарий (необязательно)", max_length=250, required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Например: приду с сопровождающим"}),
    )


class AwardPointsForm(forms.Form):
    """Points for a single enrolment; an empty value clears them."""

    points = forms.IntegerField(
        label="Баллы",
        required=False,
        min_value=0,
        max_value=10,
        widget=forms.NumberInput(attrs={"min": 0, "max": 10}),
    )
    note = forms.CharField(label="Основание", max_length=250, required=False)

    def clean_points(self):
        return self.cleaned_data.get("points")


class ApplicantFilterForm(forms.Form):
    q = forms.CharField(label="Поиск", required=False,
                        widget=forms.TextInput(attrs={"placeholder": "ФИО или почта"}))
    direction = forms.ChoiceField(label="Направление", required=False,
                                  choices=[("", "все")] + STUDY_DIRECTIONS)
    status = forms.ChoiceField(label="Статус анкеты", required=False,
                               choices=[("", "все")] + [
                                   ("draft", "черновик"), ("submitted", "подана"),
                                   ("reviewed", "рассмотрена"), ("invited", "приглашён"),
                               ])


class EventFilterForm(forms.Form):
    status = forms.ChoiceField(label="Статус", required=False,
                               choices=[("", "все")] + list(Event.STATUS_CHOICES))
    event_type = forms.ChoiceField(label="Тип", required=False,
                                   choices=[("", "все")])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_type"].choices = [("", "все")] + list(Event._meta.get_field("event_type").choices)
