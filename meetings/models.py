import secrets
import string

from django.conf import settings
from django.db import models


def generate_room_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


class Room(models.Model):
    code = models.CharField(max_length=12, unique=True, default=generate_room_code)
    title = models.CharField(max_length=200, default="SmartMeet Room")
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hosted_rooms")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.code})"


class Meeting(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="meetings")
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    # Everyone who opened the room page for this meeting (host included).
    # Used so a participant can find their meeting — and the recording they
    # saved — from their own dashboard, not just the host.
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="attended_meetings",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Meeting in {self.room.code} @ {self.started_at}"


class Recording(models.Model):
    STATUS_UPLOADED = "uploaded"
    STATUS_PROCESSING = "processing"
    STATUS_TRANSCRIBING = "transcribing"
    STATUS_SUBTITLING = "subtitling"
    STATUS_TRANSLATING = "translating"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_UPLOADED, "Uploaded"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_TRANSCRIBING, "Transcribing"),
        (STATUS_SUBTITLING, "Generating subtitles"),
        (STATUS_TRANSLATING, "Translating"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    # One row per participant-per-meeting: everybody in the call can press
    # Record and keep their OWN subtitled video instead of overwriting the
    # host's (this used to be a OneToOne, so the last upload silently deleted
    # everyone else's transcript and video).
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="recordings")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recordings",
    )
    video_file = models.FileField(upload_to="recordings/raw/")
    subtitled_video = models.FileField(upload_to="recordings/subtitled/", blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    status_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Recording for {self.meeting}"


class Transcript(models.Model):
    recording = models.OneToOneField(Recording, on_delete=models.CASCADE, related_name="transcript")
    text = models.TextField()
    segments_json = models.JSONField(default=list)
    language = models.CharField(max_length=10, default="en")
    summary_json = models.JSONField(default=dict, blank=True)
    srt_file = models.FileField(upload_to="recordings/srt/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transcript for {self.recording_id}"


class Translation(models.Model):
    transcript = models.ForeignKey(Transcript, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=10)
    translated_text = models.TextField()
    pdf_file = models.FileField(upload_to="exports/pdf/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("transcript", "language_code")

    def __str__(self):
        return f"{self.language_code} translation"
