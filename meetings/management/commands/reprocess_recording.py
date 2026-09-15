from django.core.management.base import BaseCommand

from meetings.models import Recording
from meetings.tasks import process_recording


class Command(BaseCommand):
    help = "Reprocess a recording in the foreground (shows errors in this terminal)."

    def add_arguments(self, parser):
        parser.add_argument("recording_id", type=int)

    def handle(self, *args, **options):
        rid = options["recording_id"]
        recording = Recording.objects.filter(pk=rid).first()
        if not recording:
            self.stderr.write(f"Recording {rid} not found.")
            return
        self.stdout.write(f"Processing recording {rid}...")
        process_recording(rid)
        recording.refresh_from_db()
        self.stdout.write(f"Status: {recording.status}")
        self.stdout.write(f"Message: {recording.status_message}")
