"""Fill the database with demo data for the coursework defence.

Usage:
    python manage.py seed_demo            # create or refresh demo content
    python manage.py seed_demo --reset    # delete demo content first
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.constants import ROLE_ADMIN, ROLE_APPLICANT
from accounts.models import ApplicantProfile, User
from events.models import Enrollment, Event
from events.services import recalc_bonus_points

ADMIN_EMAIL = "office@bgu.ru"
ADMIN_PASSWORD = "Office#2026bgu"
APPLICANT_PASSWORD = "Applicant#2026bgu"

APPLICANTS = [
    {"email": "badmaev.a@demo.bgu.ru", "last": "Бадмаев", "first": "Аюр", "middle": "Геннадьевич",
     "birth": "2008-03-14", "phone": "+7 902 133-45-01", "region": "г. Улан-Удэ",
     "school": "Лицей №4", "direction": "02.03.01", "rus": 78, "math": 82, "info": 85, "phys": None},
    {"email": "tsybenova.d@demo.bgu.ru", "last": "Цыбенова", "first": "Дарима", "middle": "Баторовна",
     "birth": "2008-07-02", "phone": "+7 902 561-22-77", "region": "г. Улан-Удэ",
     "school": "Школа №33", "direction": "01.03.01", "rus": 84, "math": 90, "info": 72, "phys": None},
    {"email": "ivanov.p@demo.bgu.ru", "last": "Иванов", "first": "Пётр", "middle": "Сергеевич",
     "birth": "2007-11-23", "phone": "+7 908 610-19-30",
     "region": "Республика Бурятия, п. Сосново-Озерское", "school": "СОШ №1",
     "direction": "09.03.01", "rus": 66, "math": 71, "info": 88, "phys": None},
    {"email": "nikolaeva.e@demo.bgu.ru", "last": "Николаева", "first": "Эрдэни", "middle": "Цыреновна",
     "birth": "2008-01-30", "phone": "+7 914 845-77-12", "region": "г. Улан-Удэ",
     "school": "Гимназия №3", "direction": "44.03.05", "rus": 88, "math": 76, "info": 69, "phys": None},
    {"email": "dambaev.b@demo.bgu.ru", "last": "Дамбаев", "first": "Баир", "middle": "Александрович",
     "birth": "2007-09-09", "phone": "+7 902 330-58-64",
     "region": "Забайкальский край, г. Чита", "school": "Лицей №2",
     "direction": "01.03.02", "rus": 73, "math": 85, "info": 64, "phys": 81},
    {"email": "mikhailova.a@demo.bgu.ru", "last": "Михайлова", "first": "Алина", "middle": "Игоревна",
     "birth": "2008-05-17", "phone": "+7 904 122-90-45", "region": "г. Улан-Удэ",
     "school": "Школа №48", "direction": "09.03.03", "rus": 81, "math": 74, "info": 91, "phys": None},
]


class Command(BaseCommand):
    help = "Создать демонстрационные данные: администратор, абитуриенты, мероприятия."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Удалить демо-мероприятия, записи и абитуриентов перед созданием.")
        parser.add_argument("--admin-password", default=ADMIN_PASSWORD)
        parser.add_argument("--applicant-password", default=APPLICANT_PASSWORD)

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()

        admin, created = User.objects.get_or_create(
            email=ADMIN_EMAIL,
            defaults={"role": ROLE_ADMIN, "is_staff": True, "is_superuser": True},
        )

        if options["reset"]:
            # Demo content is everything created by the demo administrator account.
            Event.objects.filter(created_by=admin).delete()
            ApplicantProfile.objects.filter(user__email__endswith="@demo.bgu.ru").delete()
            User.objects.filter(email__endswith="@demo.bgu.ru").delete()
            self.stdout.write(self.style.WARNING(
                "Удалены мероприятия демо-администратора и аккаунты абитуриентов."))

        if created:
            admin.set_password(options["admin_password"])
            admin.save(update_fields=["password"])
            self.stdout.write(f"Создан администратор {ADMIN_EMAIL}")
        else:
            self.stdout.write(f"Администратор {ADMIN_EMAIL} уже существовал (пароль не изменён)")

        events = self._create_events(admin, now)
        applicants = self._create_applicants(options["applicant_password"])
        enrollments = self._create_enrollments(events, applicants)

        for applicant in applicants:
            recalc_bonus_points(applicant)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Демо-данные готовы."))
        self.stdout.write(f"  Администратор : {admin.email} / {options['admin_password']}")
        for applicant in applicants[:3]:
            self.stdout.write(f"  Абитуриент    : {applicant.email} / {options['applicant_password']}")
        self.stdout.write(f"  Всего         : мероприятий {Event.objects.count()}, "
                          f"абитуриентов {User.objects.filter(role=ROLE_APPLICANT).count()}, "
                          f"записей {Enrollment.objects.count()}")
        self.stdout.write("  2FA при первом входе настраивается в интерфейсе — отсканируйте QR-код "
                          "приложением «Яндекс Ключ».")

    # ------------------------------------------------------------------ steps
    def _create_events(self, admin, now) -> dict[str, Event]:
        catalogue = [
            ("День открытых дверей ИМиКН", "open_day",
             "Знакомство с институтами и кафедрами, мастер-классы по математике и "
             "программированию, встреча с приёмной комиссией.",
             now + timedelta(days=9, hours=3), None, "г. Улан-Удэ, ул. Смолина, 10а, актовый зал",
             120, 5, Event.STATUS_PUBLISHED),
            ("Олимпиада «Алгебра и логика»", "olympiad",
             "Индивидуальная олимпиада для 10-11 классов. Регистрация закрывается за сутки "
             "до начала, продолжительность тура — 3 часа.",
             now + timedelta(days=18, hours=2), now + timedelta(days=18, hours=5),
             "корпус №2, ауд. 214", 40, 8, Event.STATUS_PUBLISHED),
            ("Мастер-класс: Python для абитуриентов", "lecture",
             "Практическое занятие на Python: от переменных до первого проекта. "
             "При себе ноутбук с установленным Python 3.11+.",
             now + timedelta(days=4, hours=4), now + timedelta(days=4, hours=6),
             "корпус №1, лаборатория 105", 25, 4, Event.STATUS_PUBLISHED),
            ("Подготовительные курсы: математика (суббота)", "prep_courses",
             "Курс подготовки к ЕГЭ по профильной математике, 48 академических часов. "
             "Занятия по субботам в 10:00.",
             now + timedelta(days=25, hours=1), now + timedelta(days=25, hours=3),
             "корпус №1, ауд. 12", 30, 3, Event.STATUS_PUBLISHED),
            ("Экскурсия в лабораторию искусственного интеллекта", "lab_tour",
             "Демонстрации учебных проектов: компьютерное зрение, обработка речи, "
             "системы на базе LLM.",
             now + timedelta(days=13, hours=5), None, "корпус №3, лаборатория ИИ",
             15, 5, Event.STATUS_DRAFT),
            ("Встреча «Профессии в IT Бурятии»", "lecture",
             "Панельная дискуссия с выпускниками института и работодателями.",
             now - timedelta(days=12, hours=2), None, "корпус №2, ауд. 301",
             60, 6, Event.STATUS_PUBLISHED),
            ("Олимпиада «Математический бой», осенний этап", "olympiad",
             "Командный тур: 6 задач, 2 часа, команды по 4 человека.",
             now - timedelta(days=26, hours=1), None, "корпус №2, ауд. 214",
             44, 10, Event.STATUS_PUBLISHED),
        ]

        result: dict[str, Event] = {}
        for title, type_, description, start, end, location, capacity, points, status in catalogue:
            event, was_created = Event.objects.get_or_create(
                title=title,
                defaults={
                    "event_type": type_, "description": description, "start_at": start,
                    "end_at": end, "location": location, "capacity": capacity,
                    "bonus_points": points, "status": status, "created_by": admin,
                },
            )
            result[title] = event
            if was_created:
                self.stdout.write(f"  мероприятие: {title}")
        return result

    def _create_applicants(self, password: str) -> list[User]:
        users: list[User] = []
        for data in APPLICANTS:
            user, created = User.objects.get_or_create(
                email=data["email"], defaults={"role": ROLE_APPLICANT},
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            ApplicantProfile.objects.get_or_create(
                user=user,
                defaults={
                    "last_name": data["last"], "first_name": data["first"],
                    "middle_name": data["middle"],
                    "birth_date": timezone.datetime.strptime(data["birth"], "%Y-%m-%d").date(),
                    "phone": data["phone"], "region": data["region"], "school": data["school"],
                    "direction": data["direction"], "exam_russian": data["rus"],
                    "exam_math": data["math"], "exam_info": data["info"],
                    "exam_physics": data["phys"], "status": "submitted",
                },
            )
            users.append(user)
        return users

    def _create_enrollments(self, events: dict[str, Event], applicants: list[User]) -> int:
        plan = [
            ("Встреча «Профессии в IT Бурятии»", [0, 1, 2], {0: 6, 1: 6, 2: 4}),
            ("Олимпиада «Математический бой», осенний этап", [1, 3, 4], {1: 10, 3: 7, 4: 5}),
            ("Мастер-класс: Python для абитуриентов", [0, 2, 5], {}),
            ("День открытых дверей ИМиКН", [0, 1, 3, 5], {}),
            ("Олимпиада «Алгебра и логика»", [1, 4], {}),
        ]
        created = 0
        for title, indexes, points_map in plan:
            event = events.get(title)
            if not event:
                continue
            for position, applicant_index in enumerate(indexes):
                applicant = applicants[applicant_index]
                enrollment, _ = Enrollment.objects.get_or_create(
                    event=event, applicant=applicant,
                    defaults={"note": "Демо-запись"},
                )
                created += 1
                points = points_map.get(applicant_index)
                if points is not None:
                    enrollment.status = Enrollment.STATUS_ATTENDED
                    enrollment.points = points
                    enrollment.points_note = "Диплом призёра" if points >= 7 else "Сертификат участника"
                    enrollment.points_set_at = timezone.now()
                    enrollment.points_set_by_id = event.created_by_id
                    enrollment.save(update_fields=["status", "points", "points_note",
                                                   "points_set_at", "points_set_by_id"])
        self.stdout.write(f"  записей создано/проверено: {created}")
        return created
