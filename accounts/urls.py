from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.user_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # second factor
    path("2fa/", views.two_factor, name="two_factor"),
    path("2fa/setup/", views.totp_setup, name="totp_setup"),
    path("2fa/setup/finished/", views.two_factor_done_setup, name="two_factor_done_setup"),
    path("recovery-codes/", views.recovery_show, name="recovery_show"),
    path("recovery-codes/refresh/", views.recovery_regenerate, name="recovery_regenerate"),
    path("2fa/disable/", views.totp_disable, name="totp_disable"),
    # MAX messenger
    path("max/", views.max_account, name="max_account"),
    path("max/start/", views.max_link_start, name="max_link_start"),
    path("max/unlink/", views.max_unlink, name="max_unlink"),
    path("max/test/", views.max_test_message, name="max_test_message"),
    path("max/notify/", views.max_notify_toggle, name="max_notify_toggle"),
    # cabinet
    path("profile/", views.profile, name="profile"),
    path("profile/submit/", views.submit_application, name="submit_application"),
    path("settings/", views.account_settings, name="account_settings"),
    path("settings/log/", views.login_log, name="login_log"),
]
