from django.urls import path

from . import frontend_views

urlpatterns = [
    path('', frontend_views.index, name='frontend-index'),
]
