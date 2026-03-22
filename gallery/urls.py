from django.urls import path
from . import views

app_name = 'gallery'

urlpatterns = [
    path('', views.upload_csv, name='upload'),
    path('view/', views.gallery_view, name='gallery_view'),
    path('delete/', views.delete_image, name='delete_image'),
    path('download/', views.download_csv, name='download_csv'),
    path('start-download/', views.start_download, name='start_download'),
    path('download-status/', views.download_status, name='download_status'),
    path('get-zip/', views.serve_zip, name='serve_zip'),
]
