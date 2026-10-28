from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("events/", views.catalog, name="catalog"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/enroll/", views.event_enroll, name="event_enroll"),
    path("events/<int:pk>/cancel/", views.event_cancel, name="event_cancel"),
    path("events/my/", views.my_events, name="my_events"),
    # admissions committee
    path("office/", views.admin_dashboard, name="admin_dashboard"),
    path("office/events/", views.admin_event_list, name="admin_event_list"),
    path("office/events/new/", views.admin_event_create, name="admin_event_create"),
    path("office/events/<int:pk>/", views.admin_event_detail, name="admin_event_detail"),
    path("office/events/<int:pk>/edit/", views.admin_event_update, name="admin_event_update"),
    path("office/events/<int:pk>/status/", views.admin_event_status, name="admin_event_status"),
    path("office/events/<int:pk>/delete/", views.admin_event_delete, name="admin_event_delete"),
    path("office/enrollments/<int:pk>/points/", views.admin_award_points, name="admin_award_points"),
    path("office/enrollments/<int:pk>/attended/", views.admin_mark_attended, name="admin_mark_attended"),
    path("office/applicants/", views.admin_applicant_list, name="admin_applicant_list"),
    path("office/applicants/<int:pk>/", views.admin_applicant_detail, name="admin_applicant_detail"),
    path("office/applicants/<int:pk>/status/", views.admin_applicant_status, name="admin_applicant_status"),
]
