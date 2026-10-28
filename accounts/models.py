"""User accounts, applicant data and the security related tables.

Tables of interest for the term paper:

* ``accounts_user``              - authentication + role (applicant / admin)
* ``accounts_applicantprofile``  - "applicant data" table from the spec
* ``accounts_totpdevice``        - second factor secret (Yandex.Key and alike)
* ``accounts_recoverycode``      - one-time backup codes, stored hashed
* ``accounts_onetimecode``       - short lived codes delivered via the MAX bot
* ``accounts_loginlog``          - audit trail of sign in attempts
"""
from __future__ import annotations

import secrets

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .constants import (
    APPLICATION_STATUS,
    ROLE_ADMIN,
    ROLE_APPLICANT,
    ROLE_CHOICES,
    STUDY_DIRECTIONS,
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)  # hashed with Argon2id (see settings.PASSWORD_HASHERS)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("role", ROLE_APPLICANT)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("role", ROLE_ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Login identity. E-mail is the username; ``role`` drives all access rules."""

    email = models.EmailField("Электронная почта", unique=True, db_index=True)
    role = models.CharField("Роль", max_length=16, choices=ROLE_CHOICES, default=ROLE_APPLICANT)
    is_active = models.BooleanField("Активен", default=True)
    is_staff = models.BooleanField("Доступ к админ-панели", default=False)
    date_joined = models.DateTimeField("Дата регистрации", auto_now_add=True)
    last_login = models.DateTimeField("Последний вход", null=True, blank=True)

    # MAX messenger linkage (notifications and optional second factor).
    max_user_id = models.BigIntegerField("ID в MAX", null=True, blank=True, unique=True)
    max_linked_at = models.DateTimeField("MAX привязан", null=True, blank=True)
    max_notify = models.BooleanField("Получать уведомления в MAX", default=True)
    # Short lived plaintext code typed by the applicant into the bot chat.
    max_link_code = models.CharField(
        "Код привязки MAX", max_length=16, null=True, blank=True, unique=True
    )
    max_link_expires_at = models.DateTimeField("Код привязки годен до", null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.email

    @property
    def full_display_name(self) -> str:
        profile = getattr(self, "profile", None)
        if profile and profile.full_name:
            return profile.full_name
        return self.email

    @property
    def is_applicant(self) -> bool:
        return self.role == ROLE_APPLICANT

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def max_linked(self) -> bool:
        return self.max_user_id is not None

    def issue_max_link_code(self, ttl_seconds: int = 900) -> str:
        """Replace the previous code and return the new one for the bot chat."""
        from django.utils import timezone

        code = new_link_code()
        self.max_link_code = code
        self.max_link_expires_at = timezone.now() + timezone.timedelta(seconds=ttl_seconds)
        self.save(update_fields=["max_link_code", "max_link_expires_at"])
        return code

    def clear_max_link(self) -> None:
        self.max_user_id = None
        self.max_linked_at = None
        self.max_link_code = None
        self.max_link_expires_at = None
        self.save(update_fields=["max_user_id", "max_linked_at", "max_link_code",
                                 "max_link_expires_at"])


class ApplicantProfile(models.Model):
    """The "applicant data" table required by the specification."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Пользователь",
        primary_key=True,
    )
    last_name = models.CharField("Фамилия", max_length=80, blank=True)
    first_name = models.CharField("Имя", max_length=80, blank=True)
    middle_name = models.CharField("Отчество", max_length=80, blank=True)
    birth_date = models.DateField("Дата рождения", null=True, blank=True)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    region = models.CharField("Регион / город", max_length=120, blank=True)
    school = models.CharField("Школа / колледж", max_length=160, blank=True)

    direction = models.CharField(
        "Направление подготовки", max_length=16, choices=STUDY_DIRECTIONS, blank=True
    )
    exam_russian = models.PositiveSmallIntegerField("Балл ЕГЭ: русский", null=True, blank=True)
    exam_math = models.PositiveSmallIntegerField("Балл ЕГЭ: математика", null=True, blank=True)
    exam_info = models.PositiveSmallIntegerField("Балл ЕГЭ: информатика", null=True, blank=True)
    exam_physics = models.PositiveSmallIntegerField("Балл ЕГЭ: физика", null=True, blank=True)

    status = models.CharField(
        "Статус анкеты", max_length=16, choices=APPLICATION_STATUS, default="draft"
    )
    # Points awarded by the admissions committee for participation in events.
    bonus_points = models.PositiveSmallIntegerField("Доп. баллы за мероприятия", default=0)
    comment = models.TextField("Комментарий комиссии", blank=True)

    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Изменена", auto_now=True)

    class Meta:
        db_table = "accounts_applicantprofile"
        verbose_name = "Данные абитуриента"
        verbose_name_plural = "Данные абитуриентов"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return self.full_name or self.user.email

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name, self.middle_name) if p).strip()

    @property
    def total_exam_points(self) -> int:
        return sum(
            v
            for v in (self.exam_russian, self.exam_math, self.exam_info, self.exam_physics)
            if v is not None
        )

    @property
    def is_complete(self) -> bool:
        required = (self.last_name, self.first_name, self.birth_date, self.direction)
        return all(bool(v) for v in required)


class TotpDevice(models.Model):
    """TOTP secret of one account (Yandex.Key, Google Authenticator, ...)."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="totp", verbose_name="Пользователь"
    )
    secret = models.CharField("Секрет (base32)", max_length=64)
    confirmed = models.BooleanField("Подтверждено", default=False)
    # Anti replay protection: a step may be used only once.
    last_step = models.BigIntegerField("Последний использованный шаг", default=0)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    confirmed_at = models.DateTimeField("Подтверждён", null=True, blank=True)

    class Meta:
        db_table = "accounts_totpdevice"
        verbose_name = "2FA-устройство"
        verbose_name_plural = "2FA-устройства"

    def __str__(self) -> str:
        return f"TOTP {self.user.email} ({'on' if self.confirmed else 'setup'})"


class RecoveryCode(models.Model):
    """Hashed one-time backup codes handed out right after 2FA enrolment."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField("Хеш кода", max_length=255)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    used_at = models.DateTimeField("Использован", null=True, blank=True)

    class Meta:
        db_table = "accounts_recoverycode"
        verbose_name = "Резервный код"
        verbose_name_plural = "Резервные коды"

    def __str__(self) -> str:
        return f"recovery for {self.user.email}"


class OneTimeCode(models.Model):
    """Short lived numeric code sent through the MAX bot (passwordless sign in
    and the "code in MAX" second factor)."""

    PURPOSE_LOGIN = "login"
    PURPOSE_SECOND_FACTOR = "2fa"
    PURPOSE_LINK = "link"
    PURPOSE_CHOICES = [
        (PURPOSE_LOGIN, "Вход"),
        (PURPOSE_SECOND_FACTOR, "Подтверждение входа"),
        (PURPOSE_LINK, "Привязка MAX"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField("Назначение", max_length=16, choices=PURPOSE_CHOICES)
    code_hash = models.CharField("Хеш кода", max_length=255)
    expires_at = models.DateTimeField("Годен до")
    consumed_at = models.DateTimeField("Использован", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("Попыток", default=0)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        db_table = "accounts_onetimecode"
        verbose_name = "Одноразовый код"
        verbose_name_plural = "Одноразовые коды"
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, user, purpose: str, ttl_seconds: int, digits: int = 6):
        """Create a code and return ``(instance, plaintext)``."""
        code = "".join(str(secrets.randbelow(10)) for _ in range(digits))
        instance = cls(
            user=user,
            purpose=purpose,
            code_hash=make_code_hash(code),
            expires_at=timezone.now() + timezone.timedelta(seconds=ttl_seconds),
        )
        instance.save()
        return instance, code

    @property
    def is_valid(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now() and self.attempts < 5

    def verify(self, value: str) -> bool:
        """Constant-time comparison against the stored hash, with throttling."""
        self.attempts += 1
        if not self.is_valid:
            self.save(update_fields=["attempts"])
            return False
        ok = verify_code_hash(value, self.code_hash)
        if ok:
            self.consumed_at = timezone.now()
        self.save(update_fields=["attempts", "consumed_at"])
        return ok


def make_code_hash(value: str) -> str:
    from django.contrib.auth.hashers import make_password

    return make_password(value)


def verify_code_hash(value: str, encoded: str) -> bool:
    from django.contrib.auth.hashers import check_password

    return check_password(value or "", encoded)


def new_link_code() -> str:
    return secrets.token_hex(6).upper()  # 12 hex chars, easy to type in a chat


class LoginLog(models.Model):
    """Audit trail: who tried to sign in, from where and why it failed."""

    OUTCOME_SUCCESS = "success"
    OUTCOME_BAD_PASSWORD = "bad_password"
    OUTCOME_UNKNOWN_USER = "unknown_user"
    OUTCOME_INACTIVE = "inactive"
    OUTCOME_2FA_FAIL = "2fa_fail"
    OUTCOME_2FA_SKIP = "2fa_skipped"
    OUTCOME_LOCKED = "locked"
    OUTCOME_LOGOUT = "logout"
    OUTCOME_CHOICES = [
        (OUTCOME_SUCCESS, "Успешный вход"),
        (OUTCOME_BAD_PASSWORD, "Неверный пароль"),
        (OUTCOME_UNKNOWN_USER, "Нет такого пользователя"),
        (OUTCOME_INACTIVE, "Учётная запись отключена"),
        (OUTCOME_2FA_FAIL, "Ошибка второго фактора"),
        (OUTCOME_2FA_SKIP, "Попытка обойти 2FA"),
        (OUTCOME_LOCKED, "Временная блокировка"),
        (OUTCOME_LOGOUT, "Выход"),
    ]

    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="login_logs"
    )
    identifier = models.CharField("Введённый email", max_length=254, blank=True)
    outcome = models.CharField("Результат", max_length=16, choices=OUTCOME_CHOICES)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=256, blank=True)
    created_at = models.DateTimeField("Время", auto_now_add=True, db_index=True)

    class Meta:
        db_table = "accounts_loginlog"
        verbose_name = "Журнал входов"
        verbose_name_plural = "Журнал входов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.identifier or self.user} — {self.get_outcome_display()}"


def is_admin_role(user) -> bool:
    return bool(user and user.is_authenticated and user.role == ROLE_ADMIN)


def is_applicant_role(user) -> bool:
    return bool(user and user.is_authenticated and user.role == ROLE_APPLICANT)
