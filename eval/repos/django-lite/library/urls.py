from django.urls import path

from . import views

urlpatterns = [
    path("workshops/", views.workshop_list),
    path("workshops/<int:workshop_id>/register/", views.register_view),
]
