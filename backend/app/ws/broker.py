from collections.abc import Iterable

from fastapi import WebSocket


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
        sockets: Iterable[WebSocket] = tuple(self._connections.get(user_id, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(user_id, websocket)


broker = ApplicationBroker()