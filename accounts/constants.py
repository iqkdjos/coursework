"""Reference data of the applicant system (BGU, Institute of Mathematics,
Physics and Computer Sciences).

Study programmes are listed here so that the list can be edited in one place
without touching models, forms or templates.
"""

# Role codes stored in accounts.User.role
ROLE_APPLICANT = "applicant"
ROLE_ADMIN = "admin"

ROLE_CHOICES = [
    (ROLE_APPLICANT, "Абитуриент"),
    (ROLE_ADMIN, "Администратор приёмной комиссии"),
]

# Bachelor programmes of the institute (OKSO codes).
STUDY_DIRECTIONS = [
    ("01.03.01", "01.03.01 Математика"),
    ("01.03.02", "01.03.02 Прикладные математика и физика"),
    ("02.03.01", "02.03.01 Математика и компьютерные науки"),
    ("09.03.01", "09.03.01 Информатика и вычислительная техника"),
    ("09.03.03", "09.03.03 Прикладная информатика"),
    ("44.03.05", "44.03.05 Педагогическое образование (математика и информатика)"),
]

APPLICATION_STATUS = [
    ("draft", "Черновик анкеты"),
    ("submitted", "Анкета подана"),
    ("reviewed", "Рассмотрена комиссией"),
    ("invited", "Приглашён к поступлению"),
]

EVENT_TYPES = [
    ("open_day", "День открытых дверей"),
    ("olympiad", "Олимпиада / конкурс"),
    ("lecture", "Открытая лекция / мастер-класс"),
    ("prep_courses", "Подготовительные курсы"),
    ("lab_tour", "Экскурсия в лаборатории ИМиКН"),
]

ENROLLMENT_STATUS = [
    ("enrolled", "Записан"),
    ("attended", "Посетил"),
    ("cancelled", "Отменил запись"),
]
