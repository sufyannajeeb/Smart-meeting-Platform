import csv
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Meeting, Recording, Room, Transcript, Translation
from .services.ffmpeg_service import check_ffmpeg
from .services.pdf_service import build_transcript_pdf
from .services.translate_service import translate_text
from .services.turn_service import get_cloudflare_turn_ice_servers
from .tasks import enqueue_recording_processing

User = get_user_model()


def _get_recording(meeting: Meeting) -> Recording | None:
    try:
        return meeting.recording
    except Recording.DoesNotExist:
        return None


def _get_transcript(recording: Recording | None) -> Transcript | None:
    if recording is None:
        return None
    try:
        return recording.transcript
    except Transcript.DoesNotExist:
        return None


def _redirect_target(request, meeting):
    """
    Redirect helper shared by translation-generating views. If the form
    included a hidden 'next' field (used by the voice-memo results page to
    keep the user on the same page), redirect there. Otherwise fall back to
    the standard meeting_detail page.
    """
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("meetings:meeting_detail", meeting_id=meeting.id)


@login_required
def dashboard(request):
    rooms = Room.objects.filter(host=request.user, is_active=True)[:10]
    recent_meetings = Meeting.objects.filter(host=request.user).select_related("room", "recording")[:10]
    return render(
        request,
        "meetings/dashboard.html",
        {
            "rooms": rooms,
            "recent_meetings": recent_meetings,
            "ffmpeg_ok": check_ffmpeg(),
            "languages": settings.SUPPORTED_LANGUAGES,
        },
    )


@login_required
@require_POST
def create_room(request):
    title = request.POST.get("title", "SmartMeet Room").strip() or "SmartMeet Room"
    room = Room.objects.create(host=request.user, title=title)
    messages.success(request, f"Room created: {room.code}")
    return redirect("meetings:room", code=room.code)


@login_required
@require_POST
def join_room(request):
    code = request.POST.get("code", "").strip().upper()
    room = Room.objects.filter(code=code, is_active=True).first()
    if not room:
        messages.error(request, "Room not found. Check the code and try again.")
        return redirect("meetings:dashboard")
    return redirect("meetings:room", code=room.code)


@login_required
def room_view(request, code):
    room = get_object_or_404(Room, code=code.upper(), is_active=True)
    meeting = Meeting.objects.filter(room=room, ended_at__isnull=True).first()
    if not meeting:
        meeting = Meeting.objects.create(room=room, host=room.host)

    ice_servers = list(settings.WEBRTC_ICE_SERVERS)
    cloudflare_servers = get_cloudflare_turn_ice_servers()
    if cloudflare_servers:
        ice_servers.extend(cloudflare_servers)

    return render(
        request,
        "meetings/room.html",
        {
            "room": room,
            "meeting": meeting,
            "meeting_id": meeting.id,
            "is_host": request.user == room.host,
            "username": request.user.username,
            "user_id": request.user.id,
            "ice_servers": json.dumps(ice_servers),
        },
    )


@login_required
def meeting_detail(request, meeting_id):
    meeting = get_object_or_404(Meeting.objects.select_related("room", "host"), pk=meeting_id)
    recording = _get_recording(meeting)
    transcript = _get_transcript(recording)
    translations = transcript.translations.all() if transcript else []

    return render(
        request,
        "meetings/meeting_detail.html",
        {
            "meeting": meeting,
            "recording": recording,
            "transcript": transcript,
            "translations": translations,
            "languages": settings.SUPPORTED_LANGUAGES,
        },
    )


@login_required
@require_POST
def end_meeting(request, meeting_id):
    meeting = get_object_or_404(Meeting, pk=meeting_id)
    if meeting.host != request.user and meeting.room.host != request.user:
        messages.error(request, "Only the host can end the meeting.")
        return redirect("meetings:room", code=meeting.room.code)

    meeting.ended_at = timezone.now()
    meeting.save(update_fields=["ended_at"])
    messages.info(request, "Meeting ended. Upload your recording to start AI processing.")
    return redirect("meetings:meeting_detail", meeting_id=meeting.id)


@login_required
@require_POST
def upload_recording(request, meeting_id):
    meeting = get_object_or_404(Meeting, pk=meeting_id)
    video = request.FILES.get("video")
    if not video:
        return JsonResponse({"error": "No video file provided"}, status=400)

    duration = int(request.POST.get("duration", 0))
    target_lang = request.POST.get("target_lang", "en")

    recording, created = Recording.objects.get_or_create(meeting=meeting)

    # Actively running pipeline can't be replaced mid-flight. Other states
    # (uploaded, failed, subtitling, translating, ready) may be replaced by
    # a fresh upload — this lets users recover from a stuck/stale recording.
    if not created and recording.status in (
        Recording.STATUS_PROCESSING,
        Recording.STATUS_TRANSCRIBING,
    ):
        return JsonResponse(
            {"error": "Recording is currently being transcribed. Please wait."},
            status=400,
        )

    if not created:
        # A previous recording is being replaced: drop the old transcript and
        # its translations so the new audio is transcribed from scratch.
        try:
            transcript = recording.transcript
            if transcript:
                transcript.translations.all().delete()
                transcript.srt_file.delete(save=False)
                transcript.delete()
        except Transcript.DoesNotExist:
            pass
        recording.subtitled_video.delete(save=False)

    recording.video_file = video
    recording.duration_seconds = duration
    recording.status = Recording.STATUS_UPLOADED
    recording.status_message = "Upload received. Starting AI pipeline..."
    recording.save()

    enqueue_recording_processing(recording.id, target_lang)

    return JsonResponse(
        {
            "ok": True,
            "recording_id": recording.id,
            "redirect": f"/meetings/{meeting.id}/",
        }
    )


@login_required
@require_POST
def retry_processing(request, recording_id):
    recording = get_object_or_404(Recording, pk=recording_id)
    if not recording.video_file:
        messages.error(request, "No video file to process.")
        return redirect("meetings:meeting_detail", meeting_id=recording.meeting_id)

    recording.status = Recording.STATUS_UPLOADED
    recording.status_message = "Retrying AI pipeline..."
    recording.save(update_fields=["status", "status_message", "updated_at"])
    enqueue_recording_processing(recording.id)
    messages.info(request, "Processing restarted. Please wait...")
    return redirect("meetings:meeting_detail", meeting_id=recording.meeting_id)


@login_required
@require_GET
def recording_status(request, recording_id):
    recording = get_object_or_404(Recording, pk=recording_id)
    transcript = _get_transcript(recording)
    data = {
        "status": recording.status,
        "status_message": recording.status_message,
        "has_subtitled": bool(recording.subtitled_video),
        "has_transcript": transcript is not None,
        "has_summary": bool(transcript and transcript.summary_json.get("summary")),
    }
    if transcript:
        data["transcript_preview"] = transcript.text[:500]
    return JsonResponse(data)


@login_required
@require_POST
def translate_transcript(request, meeting_id):
    meeting = get_object_or_404(Meeting, pk=meeting_id)
    recording = _get_recording(meeting)
    transcript = _get_transcript(recording)
    if not transcript:
        messages.error(request, "Transcript not ready yet.")
        return _redirect_target(request, meeting)

    lang = request.POST.get("language", "hi")
    lang_map = dict(settings.SUPPORTED_LANGUAGES)

    existing = Translation.objects.filter(transcript=transcript, language_code=lang).first()
    if existing and existing.translated_text:
        translated = existing.translated_text
    else:
        try:
            translated = translate_text(transcript.text, lang, transcript.language)
        except Exception as exc:
            messages.error(
                request,
                f"Translation failed ({lang_map.get(lang, lang)}): {exc}. "
                "Please wait a few seconds and try again.",
            )
            return _redirect_target(request, meeting)

    Translation.objects.update_or_create(
        transcript=transcript,
        language_code=lang,
        defaults={"translated_text": translated},
    )
    messages.success(request, f"Translated to {lang_map.get(lang, lang)}.")
    return _redirect_target(request, meeting)


@login_required
@require_POST
def generate_translation(request, meeting_id):
    meeting = get_object_or_404(Meeting, pk=meeting_id)
    recording = _get_recording(meeting)
    transcript = _get_transcript(recording)
    if not transcript:
        messages.error(request, "Transcript not ready yet.")
        return _redirect_target(request, meeting)

    lang = request.POST.get("language", "hi")
    lang_map = dict(settings.SUPPORTED_LANGUAGES)

    existing = Translation.objects.filter(transcript=transcript, language_code=lang).first()
    if existing and existing.translated_text:
        translated = existing.translated_text
    else:
        try:
            translated = translate_text(transcript.text, lang, transcript.language)
        except Exception as exc:
            messages.error(
                request,
                f"Translation failed ({lang_map.get(lang, lang)}): {exc}. "
                "Please wait a few seconds and try again.",
            )
            return _redirect_target(request, meeting)

    translation, _ = Translation.objects.update_or_create(
        transcript=transcript,
        language_code=lang,
        defaults={"translated_text": translated},
    )

    try:
        pdf_bytes = build_transcript_pdf(
            title=meeting.room.title,
            room_code=meeting.room.code,
            host_name=meeting.host.get_username(),
            language_label=lang_map.get(lang, lang),
            transcript_text=translated,
            meeting_date=meeting.started_at,
        )
    except Exception as exc:
        messages.error(request, f"PDF generation failed: {exc}.")
        return _redirect_target(request, meeting)

    translation.pdf_file.save(
        f"transcript_{recording.id}_{lang}.pdf",
        ContentFile(pdf_bytes),
        save=True,
    )

    messages.success(request, f"PDF generated in {lang_map.get(lang, lang)}.")
    return _redirect_target(request, meeting)


@login_required
def transcribe_upload(request):
    if request.method == "POST":
        audio = request.FILES.get("audio")
        title = request.POST.get("title", "Voice Memo").strip() or "Voice Memo"
        target_lang = request.POST.get("target_lang", "en")

        if not audio:
            messages.error(request, "Please choose an audio file to upload.")
            return redirect("meetings:transcribe_upload")

        if not check_ffmpeg():
            messages.error(request, "FFmpeg is not installed. Cannot process audio.")
            return redirect("meetings:transcribe_upload")

        # Voice memos don't need a live room — create an inactive room + a
        # meeting that's already "ended" so it flows straight into processing.
        room = Room.objects.create(host=request.user, title=title, is_active=False)
        now = timezone.now()
        meeting = Meeting.objects.create(
            room=room,
            host=request.user,
            started_at=now,
            ended_at=now,
        )

        recording = Recording.objects.create(meeting=meeting)
        recording.video_file = audio
        recording.duration_seconds = int(request.POST.get("duration", 0))
        recording.status = Recording.STATUS_UPLOADED
        recording.status_message = "Voice memo received. Starting AI pipeline..."
        recording.save()

        enqueue_recording_processing(recording.id, target_lang)

        messages.success(request, "Voice memo uploaded. Transcription in progress.")
        # Redirect (PRG pattern) back to this same view, now with a
        # meeting_id so the GET branch below can render the results.
        return redirect(f"{reverse('meetings:transcribe_upload')}?meeting_id={meeting.id}")

    # --- GET ---
    meeting = None
    recording = None
    transcript = None
    translations = []

    meeting_id = request.GET.get("meeting_id")
    if meeting_id:
        meeting = get_object_or_404(Meeting, pk=meeting_id, host=request.user)
        recording = _get_recording(meeting)
        transcript = _get_transcript(recording)
        translations = transcript.translations.all() if transcript else []

    return render(
        request,
        "meetings/transcribe_upload.html",
        {
            "ffmpeg_ok": check_ffmpeg(),
            "languages": settings.SUPPORTED_LANGUAGES,
            "meeting": meeting,
            "recording": recording,
            "transcript": transcript,
            "translations": translations,
        },
    )


@login_required
def download_subtitled_video(request, recording_id):
    recording = get_object_or_404(Recording, pk=recording_id)
    if not recording.subtitled_video:
        raise Http404("Subtitled video not available")
    return FileResponse(recording.subtitled_video.open("rb"), as_attachment=True, filename=f"smartmeet_{recording_id}.mp4")


@login_required
def download_pdf(request, translation_id):
    translation = get_object_or_404(
        Translation,
        pk=translation_id,
        transcript__recording__meeting__host=request.user,
    )
    if not translation.pdf_file:
        raise Http404("PDF not available")
    return FileResponse(
        translation.pdf_file.open("rb"),
        as_attachment=True,
        filename=f"smartmeet_transcript_{translation.language_code}.pdf",
    )


@login_required
def download_srt(request, recording_id):
    recording = get_object_or_404(Recording, pk=recording_id)
    transcript = _get_transcript(recording)
    if not transcript or not transcript.srt_file:
        raise Http404("SRT not available")
    return FileResponse(
        transcript.srt_file.open("rb"),
        as_attachment=True,
        filename=f"smartmeet_{recording_id}.srt",
    )


@staff_member_required
def admin_panel(request):
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ---- Top-level stats ----
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    staff_users = User.objects.filter(is_staff=True).count()
    new_users_week = User.objects.filter(date_joined__gte=week_ago).count()

    total_rooms = Room.objects.count()
    active_rooms = Room.objects.filter(is_active=True).count()
    rooms_week = Room.objects.filter(created_at__gte=week_ago).count()

    total_meetings = Meeting.objects.count()
    ongoing_meetings = Meeting.objects.filter(ended_at__isnull=True).count()
    meetings_today = Meeting.objects.filter(started_at__gte=today_start).count()

    total_recordings = Recording.objects.count()
    recordings_by_status = list(
        Recording.objects.values("status").annotate(count=Count("id")).order_by("-count")
    )
    status_labels = dict(Recording.STATUS_CHOICES)
    for row in recordings_by_status:
        row["label"] = status_labels.get(row["status"], row["status"])
    max_status_count = max((r["count"] for r in recordings_by_status), default=0)

    total_transcripts = Transcript.objects.count()
    total_translations = Translation.objects.count()

    # ---- Duration & success-rate stats ----
    total_duration_seconds = Recording.objects.aggregate(total=Sum("duration_seconds"))["total"] or 0
    total_duration_hours = round(total_duration_seconds / 3600, 1)

    avg_recording_seconds = Recording.objects.aggregate(avg=Avg("duration_seconds"))["avg"] or 0
    avg_recording_minutes = round(avg_recording_seconds / 60, 1)

    ended_meetings = Meeting.objects.filter(ended_at__isnull=False).annotate(
        duration=ExpressionWrapper(F("ended_at") - F("started_at"), output_field=DurationField())
    )
    avg_meeting_duration = ended_meetings.aggregate(avg=Avg("duration"))["avg"]
    avg_meeting_minutes = round(avg_meeting_duration.total_seconds() / 60, 1) if avg_meeting_duration else 0

    ready_count = Recording.objects.filter(status=Recording.STATUS_READY).count()
    failed_count = Recording.objects.filter(status=Recording.STATUS_FAILED).count()
    success_rate = round((ready_count / total_recordings * 100), 1) if total_recordings else 0

    # ---- Top hosts / most active hosts ----
    top_hosts = (
        Room.objects.values("host__username")
        .annotate(room_count=Count("id"))
        .order_by("-room_count")[:5]
    )
    max_top_host_count = max((h["room_count"] for h in top_hosts), default=0)

    most_active_hosts = (
        Meeting.objects.values("host__username")
        .annotate(meeting_count=Count("id"))
        .order_by("-meeting_count")[:5]
    )
    max_active_host_count = max((h["meeting_count"] for h in most_active_hosts), default=0)

    # ---- Language / translation breakdown ----
    lang_map = dict(settings.SUPPORTED_LANGUAGES)
    language_breakdown = list(
        Translation.objects.values("language_code").annotate(count=Count("id")).order_by("-count")
    )
    for row in language_breakdown:
        row["label"] = lang_map.get(row["language_code"], row["language_code"])

    # ---- Signup trend, last 30 days ----
    signup_qs = (
        User.objects.filter(date_joined__gte=now - timedelta(days=29))
        .annotate(day=TruncDate("date_joined"))
        .values("day")
        .annotate(count=Count("id"))
    )
    signup_map = {row["day"]: row["count"] for row in signup_qs}
    signup_labels, signup_counts = [], []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        signup_labels.append(day.strftime("%b %d"))
        signup_counts.append(signup_map.get(day, 0))

    # ---- Meetings trend, last 14 days ----
    meeting_qs = (
        Meeting.objects.filter(started_at__gte=now - timedelta(days=13))
        .annotate(day=TruncDate("started_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    meeting_map = {row["day"]: row["count"] for row in meeting_qs}
    meeting_labels, meeting_counts = [], []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date()
        meeting_labels.append(day.strftime("%b %d"))
        meeting_counts.append(meeting_map.get(day, 0))

    recent_users = User.objects.order_by("-date_joined")[:8]
    recent_meetings = Meeting.objects.select_related("room", "host").order_by("-started_at")[:8]

    # ---- Users tab (search + pagination) ----
    q = request.GET.get("q", "").strip()
    users_qs = User.objects.all().order_by("-date_joined")
    if q:
        users_qs = users_qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
    paginator = Paginator(users_qs, 15)
    users_page = paginator.get_page(request.GET.get("page", 1))

    context = {
        "total_users": total_users,
        "active_users": active_users,
        "staff_users": staff_users,
        "new_users_week": new_users_week,
        "total_rooms": total_rooms,
        "active_rooms": active_rooms,
        "rooms_week": rooms_week,
        "total_meetings": total_meetings,
        "ongoing_meetings": ongoing_meetings,
        "meetings_today": meetings_today,
        "total_recordings": total_recordings,
        "recordings_by_status": recordings_by_status,
        "max_status_count": max_status_count,
        "total_transcripts": total_transcripts,
        "total_translations": total_translations,
        "total_duration_hours": total_duration_hours,
        "avg_recording_minutes": avg_recording_minutes,
        "avg_meeting_minutes": avg_meeting_minutes,
        "success_rate": success_rate,
        "failed_count": failed_count,
        "top_hosts": top_hosts,
        "max_top_host_count": max_top_host_count,
        "most_active_hosts": most_active_hosts,
        "max_active_host_count": max_active_host_count,
        "language_breakdown": language_breakdown,
        "signup_labels": signup_labels,
        "signup_counts": signup_counts,
        "meeting_labels": meeting_labels,
        "meeting_counts": meeting_counts,
        "recent_users": recent_users,
        "recent_meetings": recent_meetings,
        "users_page": users_page,
        "search_query": q,
        "status_chart_labels": [r["label"] for r in recordings_by_status],
        "status_chart_counts": [r["count"] for r in recordings_by_status],
        "lang_chart_labels": [r["label"] for r in language_breakdown],
        "lang_chart_counts": [r["count"] for r in language_breakdown],
    }
    return render(request, "meetings/admin_panel.html", context)


@staff_member_required
def admin_export_users(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="smartmeet_users.csv"'
    writer = csv.writer(response)
    writer.writerow(["Username", "Email", "Date Joined", "Is Active", "Is Staff", "Is Superuser"])
    for u in User.objects.all().order_by("-date_joined"):
        writer.writerow([
            u.username,
            u.email,
            u.date_joined.strftime("%Y-%m-%d %H:%M"),
            u.is_active,
            u.is_staff,
            u.is_superuser,
        ])
    return response


@staff_member_required
@require_POST
def admin_toggle_user_active(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        messages.error(request, "You can't deactivate your own account.")
        return redirect(f"{reverse('meetings:admin_panel')}?tab=users")
    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])
    messages.success(request, f"{target.username} is now {'active' if target.is_active else 'inactive'}.")
    return redirect(f"{reverse('meetings:admin_panel')}?tab=users")


@staff_member_required
@require_POST
def admin_toggle_user_staff(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Only superusers can change staff access.")
        return redirect(f"{reverse('meetings:admin_panel')}?tab=users")
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        messages.error(request, "You can't change your own staff status.")
        return redirect(f"{reverse('meetings:admin_panel')}?tab=users")
    target.is_staff = not target.is_staff
    target.save(update_fields=["is_staff"])
    messages.success(request, f"{target.username} staff access {'granted' if target.is_staff else 'revoked'}.")
    return redirect(f"{reverse('meetings:admin_panel')}?tab=users")


@staff_member_required
@require_POST
def admin_delete_user(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Only superusers can delete users.")
        return redirect(f"{reverse('meetings:admin_panel')}?tab=users")
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        messages.error(request, "You can't delete your own account.")
        return redirect(f"{reverse('meetings:admin_panel')}?tab=users")
    username = target.username
    target.delete()
    messages.success(request, f"User {username} deleted.")
    return redirect(f"{reverse('meetings:admin_panel')}?tab=users")