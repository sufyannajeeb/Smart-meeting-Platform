import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Meeting, Recording, Room, Transcript

User = get_user_model()
TMP_MEDIA = tempfile.mkdtemp(prefix="smartmeet_test_media_")


def _fake_video(name="meeting.webm"):
    return SimpleUploadedFile(name, b"fake-webm-bytes", content_type="video/webm")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ParticipantAccessTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(username="host", password="pw")
        self.guest = User.objects.create_user(username="guest", password="pw")
        self.room = Room.objects.create(host=self.host, title="Room")

    def test_room_view_registers_participant(self):
        self.client.force_login(self.guest)
        self.client.get(reverse("meetings:room", args=[self.room.code]))

        meeting = Meeting.objects.get(room=self.room, ended_at__isnull=True)
        # Only the browser that actually opened the page is registered.
        self.assertIn(self.guest, meeting.participants.all())
        self.assertNotIn(self.host, meeting.participants.all())

    def test_dashboard_lists_joined_meetings(self):
        meeting = Meeting.objects.create(room=self.room, host=self.host)
        meeting.participants.add(self.guest)

        self.client.force_login(self.guest)
        response = self.client.get(reverse("meetings:dashboard"))

        self.assertContains(response, self.room.title)
        self.assertEqual(len(response.context["recent_meetings"]), 1)
        self.assertFalse(response.context["recent_meetings"][0].is_host)

    def test_meeting_detail_renders_every_recording(self):
        meeting = Meeting.objects.create(room=self.room, host=self.host)
        for user in (self.host, self.guest):
            rec = Recording.objects.create(meeting=meeting, recorded_by=user)
            Transcript.objects.create(recording=rec, text="hello world")

        self.client.force_login(self.guest)
        response = self.client.get(reverse("meetings:meeting_detail", args=[meeting.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["recordings"]), 2)
        self.assertContains(response, "participants saved a recording")
        self.assertContains(response, "by host")
        self.assertContains(response, "by guest")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PerParticipantRecordingTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(username="host", password="pw")
        self.guest = User.objects.create_user(username="guest", password="pw")
        self.room = Room.objects.create(host=self.host, title="Room")
        self.meeting = Meeting.objects.create(room=self.room, host=self.host)

    def _upload(self, user):
        self.client.force_login(user)
        with patch("meetings.views.enqueue_recording_processing") as enqueue:
            response = self.client.post(
                reverse("meetings:upload_recording", args=[self.meeting.id]),
                {"video": _fake_video(), "duration": "12", "target_lang": "hi"},
            )
        self.assertEqual(response.status_code, 200)
        enqueue.assert_called_once()
        return response

    def test_second_participant_upload_does_not_delete_the_first(self):
        self._upload(self.host)
        host_recording = Recording.objects.get(meeting=self.meeting, recorded_by=self.host)
        Transcript.objects.create(recording=host_recording, text="host was here")

        self._upload(self.guest)

        # Both recordings survive, each with its own transcript intact.
        self.assertEqual(Recording.objects.filter(meeting=self.meeting).count(), 2)
        host_recording.refresh_from_db()
        self.assertEqual(host_recording.transcript.text, "host was here")

    def test_reupload_by_same_user_reuses_their_row(self):
        self._upload(self.host)
        self._upload(self.host)
        self.assertEqual(Recording.objects.filter(meeting=self.meeting).count(), 1)

    def test_each_recording_is_processed_separately(self):
        self._upload(self.host)
        self._upload(self.guest)
        ids = set(Recording.objects.filter(meeting=self.meeting).values_list("id", flat=True))
        self.assertEqual(len(ids), 2)
