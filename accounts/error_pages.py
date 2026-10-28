"""Custom error pages so that no framework detail leaks to the client."""
from django.shortcuts import render


def _page(request, template, status, message):
    return render(
        request,
        template,
        {"page_title": message[0], "error_message": message[1], "status": status},
        status=status,
    )


def bad_request(request, exception=None):
    return _page(request, "errors/400.html", 400,
                 ("Запрос отклонён", "Запрос испорчен или не прошёл проверку CSRF-защиты."))


def permission_denied(request, exception=None):
    return _page(request, "errors/403.html", 403,
                 ("Доступ запрещён",
                  "У вашей роли нет прав на этот раздел. Если вы администратор — "
                  "используйте раздел «Приёмная комиссия»."))


def page_not_found(request, exception=None):
    return _page(request, "errors/404.html", 404,
                 ("Страница не найдена", "Такой страницы или мероприятия не существует."))


def server_error(request):
    return _page(request, "errors/500.html", 500,
                 ("Внутренняя ошибка", "Ошибка уже в журнале сервера. Попробуйте позже."))
