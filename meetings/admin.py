from django.contrib import admin

from .models import Meeting, Recording, Room, Transcript, Translation


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "host", "is_active", "created_at")
    search_fields = ("code", "title", "host__username")
    list_filter = ("is_active",)


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "host", "started_at", "ended_at")
    list_filter = ("started_at",)
    search_fields = ("room__code", "host__username")


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "status", "duration_seconds", "created_at")
    list_filter = ("status",)
    readonly_fields = ("status_message", "created_at", "updated_at")


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display = ("recording", "language", "created_at")
    search_fields = ("text",)


@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    list_display = ("transcript", "language_code", "created_at")
