from django.urls import path

from . import views

app_name = "sofreh"

urlpatterns = [
    path("", views.status, name="status"),
]
