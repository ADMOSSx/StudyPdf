"""End-to-end test of the server logic with in-memory fake sockets (no network, no websockets package needed)."""
import asyncio
import base64
import hashlib
import json
import tempfile
from pathlib import Path

from database.server_db import ServerDatabase
from server.config import ServerConfig
from server.rooms.manager import RoomManager
from server.websocket.handler import ConnectionHandler


class FakeWS:
    remote_address = ("127.0.0.1", 1)

    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed = True
        await self.inbox.put(None)

    def push(self, **msg):
        self.inbox.put_nowait(json.dumps(msg))

    def of(self, t):
        return [m for m in self.sent if m["t"] == t]


async def settle():
    await asyncio.sleep(0.05)


def test_full_flow():
    async def run():
        tmp = Path(tempfile.mkdtemp())
        cfg = ServerConfig(data_dir=tmp)
        manager = RoomManager(cfg, ServerDatabase(tmp / "db.sqlite"))
        h = ConnectionHandler(cfg, manager)
        pdf = b"%PDF-1.4\n" + b"x" * 1000
        host_ws, guest_ws = FakeWS(), FakeWS()
        t1, t2 = asyncio.create_task(h.handle(host_ws)), asyncio.create_task(h.handle(guest_ws))

        host_ws.push(t="create_room", user_name="Ahmed", room_name="Math", pdf={
            "name": "w.pdf", "size": len(pdf), "pages": 2, "sha256": hashlib.sha256(pdf).hexdigest()})
        await settle()
        joined = host_ws.of("joined")[0]
        code, host_id = joined["room"]["code"], joined["you"]
        assert len(code) == 7 and joined["token"]

        host_ws.push(t="pdf_chunk", seq=0, total=1, data=base64.b64encode(pdf).decode())
        await settle()
        assert manager.pdf_path(code).read_bytes() == pdf

        guest_ws.push(t="join_room", code=code.lower().replace("-", ""), user_name="Omar")
        await settle()
        gj = guest_ws.of("joined")[0]
        assert gj["pdf"]["ready"] and len(gj["members"]) == 2
        assert host_ws.of("user_joined")

        ann = {"id": "ann00001", "kind": "pen", "page": 0, "pts": [1, 1, 5, 5], "style": {"color": "#ff0000", "width": 2}}
        host_ws.push(t="annotation_created", op="o1", ann=ann)
        await settle()
        assert guest_ws.of("annotation_created")[0]["ann"]["id"] == "ann00001"
        assert host_ws.of("ack")[-1]["ok"]

        # concurrent: guest deletes, host's move arrives afterwards -> rejected, state stays consistent
        guest_ws.push(t="annotation_deleted", op="g1", id="ann00001")
        host_ws.push(t="annotation_updated", op="o2", id="ann00001", patch={"pts": [9, 9, 9, 9]})
        await settle()
        assert host_ws.of("ack")[-1]["ok"] is False and host_ws.of("ack")[-1]["reason"] == "missing"
        assert "ann00001" not in manager.get(code).annotations

        # permissions are enforced on the server, whatever the client UI says
        guest_ws.push(t="set_settings", settings={"can_draw": False})
        await settle()
        assert guest_ws.of("error")[-1]["code"] == "forbidden"
        host_ws.push(t="set_settings", settings={"can_draw": False})
        await settle()
        guest_ws.push(t="annotation_created", op="g2", ann=dict(ann, id="ann00002"))
        await settle()
        assert guest_ws.of("ack")[-1]["reason"] == "forbidden"


        # whiteboards: the server orders boards, annotations on a board need the board to exist first
        guest_ws.push(t="board_add", title="Question 1", cid="c1")
        await settle()
        assert guest_ws.of("error")[-1]["code"] == "forbidden"            # can_draw was switched off above
        host_ws.push(t="board_add", title="Question 1", cid="c1")
        await settle()
        added = guest_ws.of("board_added")[-1]
        assert added["index"] == 0 and added["cid"] == "c1" and added["board"]["title"] == "Question 1"
        assert host_ws.of("board_added")[-1]["cid"] == "c1"                 # creator gets it too (agrees on the index)
        host_ws.push(t="annotation_created", op="b1", ann=dict(ann, id="board0001", page=10000))
        host_ws.push(t="annotation_created", op="b2", ann=dict(ann, id="board0002", page=10001))
        await settle()
        acks = [a for a in host_ws.of("ack") if a["op"] in ("b1", "b2")]
        assert [a["ok"] for a in acks] == [True, False]
        assert guest_ws.of("annotation_created")[-1]["ann"]["page"] == 10000
        assert host_ws.of("joined")[0]["boards"] == []

        # chat is sanitised and broadcast to everyone
        guest_ws.push(t="chat", text="hi \x00there")
        await settle()
        assert host_ws.of("chat")[-1]["msg"]["text"] == "hi there"

        # garbage never crashes the server
        guest_ws.inbox.put_nowait("not json")
        guest_ws.push(t="annotation_created", ann="nope")
        await settle()

        # reconnect with token restores the same identity
        old_token = joined["token"]
        host2 = FakeWS()
        t3 = asyncio.create_task(h.handle(host2))
        host2.push(t="join_room", code=code, token=old_token)
        await settle()
        assert host2.of("joined")[0]["you"] == host_id

        # kick + end session
        host2.push(t="kick", id=gj["you"])
        await settle()
        assert guest_ws.of("kicked")
        host2.push(t="end_session")
        await asyncio.sleep(0.6)
        assert manager.get(code) is None
        for w in (host_ws, guest_ws, host2):
            await w.close()
        await asyncio.gather(t1, t2, t3, return_exceptions=True)
    asyncio.run(run())
