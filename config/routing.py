from django.urls import re_path

from meetings.consumers import SignalingConsumer

websocket_urlpatterns = [
    re_path(r"ws/room/(?P<room_code>\w+)/$", SignalingConsumer.as_asgi()),
]
