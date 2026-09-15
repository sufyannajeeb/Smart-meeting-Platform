from django.urls import path

from . import views

app_name = "meetings"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("rooms/create/", views.create_room, name="create_room"),
    path("rooms/join/", views.join_room, name="join_room"),
    path("room/<str:code>/", views.room_view, name="room"),
    path("meetings/<int:meeting_id>/", views.meeting_detail, name="meeting_detail"),
    path("meetings/<int:meeting_id>/end/", views.end_meeting, name="end_meeting"),
    path("meetings/<int:meeting_id>/upload/", views.upload_recording, name="upload_recording"),
    path("meetings/<int:meeting_id>/translate/", views.generate_translation, name="generate_translation"),
    path("meetings/<int:meeting_id>/translate-only/", views.translate_transcript, name="translate_transcript"),
    path("recordings/<int:recording_id>/retry/", views.retry_processing, name="retry_processing"),
    path("recordings/<int:recording_id>/status/", views.recording_status, name="recording_status"),
    path("recordings/<int:recording_id>/video/", views.download_subtitled_video, name="download_video"),
    path("recordings/<int:recording_id>/srt/", views.download_srt, name="download_srt"),
    path("translations/<int:translation_id>/pdf/", views.download_pdf, name="download_pdf"),
    path('transcribe/', views.transcribe_upload, name='transcribe_upload'),
    path("admin-panel/", views.admin_panel, name="admin_panel"),
    path("admin-panel/users/<int:user_id>/toggle-active/", views.admin_toggle_user_active, name="admin_toggle_user_active"),
    path("admin-panel/users/<int:user_id>/toggle-staff/", views.admin_toggle_user_staff, name="admin_toggle_user_staff"),
    path("admin-panel/users/<int:user_id>/delete/", views.admin_delete_user, name="admin_delete_user"),
    path("admin-panel/export-users/", views.admin_export_users, name="admin_export_users"),
]
