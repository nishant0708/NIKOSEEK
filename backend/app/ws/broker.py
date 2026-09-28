import logging
from collections.abc import Iterable

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)


class ApplicationBroker:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        # jsonable_encoder turns datetimes etc. into JSON-safe values. Without it send_json raised
        # TypeError on every update and the socket was silently dropped -> the Live Workflow
        # page only changed after a manual refresh.
        message = jsonable_encoder(payload)
        sockets: Iterable[WebSocket] = tuple(self._connections.get(user_id, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except Exception:
                logger.info("Dropping closed websocket for user_id=%s", user_id)
                self.disconnect(user_id, websocket)


broker = ApplicationBroker()
