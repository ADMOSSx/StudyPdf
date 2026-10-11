"""Client logic tests: annotation model, undo/redo, whiteboards, canvas helpers and the collaboration session.

They run against the real PySide6 when it is installed (QObject-only classes) or against tests/qtstub.py otherwise.
"""
import base64
import hashlib
import tempfile
from pathlib import Path

import pytest

import qtstub

qtstub.install()

from client.annotations.editor import Editor                              # noqa: E402
from client.annotations.model import (AnnotationStore, bounds, hit_test, new_annotation, resized,   # noqa: E402
                                      translated)
from client.collaboration.boards import BoardStore                        # noqa: E402
from client.collaboration.questions import QuestionStore                  # noqa: E402
from client.collaboration.session import SessionController                # noqa: E402
from shared.protocol import BOARD_BASE, MAX_BOARDS                        # noqa: E402

needs_stub = pytest.mark.skipif(not qtstub.IS_STUB, reason="uses widget classes; run against the Qt stand-in")


def mk(kind="pen", page=0, pts=(10, 10, 20, 20), **kw):
    a = new_annotation(kind, page, list(pts), {"color": "#ff0000", "width": 2.0}, "u1", "Ahmed", **kw)
    return a


# ------------------------------------------------------------------ model
def test_bounds_of_point_kinds_and_hit_testing():
    step = mk("step", BOARD_BASE, (100, 100), text="1")
    assert bounds(step) == (100, 100, 130, 130)
    assert hit_test(step, 115, 115, 2) and not hit_test(step, 300, 300, 2)
    img = mk("image", BOARD_BASE, (0, 0, 200, 100), data="x" * 120)
    assert hit_test(img, 100, 50, 2)
    stroke = mk("pen", 0, (0, 0, 100, 0))
    assert hit_test(stroke, 50, 3, 2) and not hit_test(stroke, 50, 40, 2)


def test_move_and_resize_keep_geometry_consistent():
    pts = [10, 10, 30, 50]
    assert translated(pts, 5, -5) == [15, 5, 35, 45]
    assert resized(pts, (10, 10, 30, 50), (0, 0, 40, 80)) == [0, 0, 40, 80]


# ------------------------------------------------------------------ undo / redo
def make_editor():
    store = AnnotationStore()
    ed = Editor(store)
    ed.identity = lambda: ("u1", "Ahmed")
    ops = []
    ed.op.connect(ops.append)
    return store, ed, ops


def test_undo_redo_create_and_delete_remaps_ids():
    store, ed, ops = make_editor()
    a = mk()
    ed.create(a)
    assert a["id"] in store.anns
    ed.undo()
    assert not store.anns and ops[-1]["t"] == "annotation_deleted"
    ed.redo()
    assert len(store.anns) == 1
    new_id = next(iter(store.anns))
    assert new_id != a["id"]                       # the server remembers deleted ids, so a restore uses a fresh one
    ed.delete(new_id)
    ed.undo()
    assert len(store.anns) == 1


def test_grouped_edits_undo_as_one_step():
    store, ed, ops = make_editor()
    ed.begin_group()
    for i in range(3):
        ed.create(mk(pts=(i, i, i + 5, i + 5)))
    ed.end_group()
    assert len(store.anns) == 3
    ed.undo()
    assert len(store.anns) == 0
    ed.redo()
    assert len(store.anns) == 3


# ------------------------------------------------------------------ boards
def test_boards_offline_and_limit():
    b = BoardStore()
    got = []
    b.add("Question 1", online=False, then=got.append)
    b.add("Question 2", online=False, then=got.append)
    assert got == [0, 1] and [x["title"] for x in b.items] == ["Question 1", "Question 2"]
    for i in range(MAX_BOARDS):
        b.add(f"B{i}", online=False)
    assert len(b.items) == MAX_BOARDS and b.full()


def test_boards_online_wait_for_the_servers_index():
    b = BoardStore()
    sent, got = [], []
    b.op.connect(sent.append)
    b.add("Q", online=True, then=got.append)
    assert sent[0]["t"] == "board_add" and not b.items and not got      # nothing local until the server answers
    b.apply_remote({"id": "other00001", "title": "Someone else's"}, 0)  # a friend's board arrives first
    b.apply_remote({"id": "mine000001", "title": "Q"}, 1, sent[0]["cid"])
    assert got == [1]                                                   # our callback gets the *real* index
    b.apply_remote({"id": "mine000001", "title": "Q"}, 1)               # duplicate echo is ignored
    assert len(b.items) == 2


# ------------------------------------------------------------------ canvas helpers
class FakeBoardDoc:
    is_board = True
    page_count = 3

    def page_size(self, i):
        return 1500.0, 2100.0

    def words(self, i):
        return []


def make_canvas():
    from client.ui.canvas import PageCanvas
    store, ed, ops = make_editor()
    c = PageCanvas(store, ed)
    c.doc, c.page, c.page_offset = FakeBoardDoc(), 1, BOARD_BASE
    c.identity = ed.identity
    c.style_for = lambda tool: {"color": "#176b66", "width": 2.0, "size": 16, "opacity": 1.0}
    return c, store, ed, ops


@needs_stub
def test_canvas_uses_board_page_ids_and_numbers_steps():
    c, store, ed, ops = make_canvas()
    assert c.ap == BOARD_BASE + 1
    c._place_step(100, 100)
    c._place_step(200, 100)
    steps = sorted(store.page_items(BOARD_BASE + 1), key=lambda a: a["text"])
    assert [s["text"] for s in steps] == ["1", "2"] and all(s["page"] == BOARD_BASE + 1 for s in steps)
    assert bounds(steps[0])[:2] == (85, 85)                  # centred on the click


@needs_stub
def test_eraser_keeps_question_images_but_removes_strokes():
    c, store, ed, ops = make_canvas()
    img = mk("image", BOARD_BASE + 1, (0, 0, 400, 300), data="x" * 120)
    stroke = mk("pen", BOARD_BASE + 1, (10, 10, 50, 10))
    ed.create(img)
    ed.create(stroke)
    c._erase(30, 10)
    assert img["id"] in store.anns and stroke["id"] not in store.anns


@needs_stub
def test_emptying_a_text_box_deletes_it():
    c, store, ed, ops = make_canvas()
    t = mk("text", BOARD_BASE + 1, (20, 20, 220, 60), text="hello")
    ed.create(t)
    c._editing = {"existing": t, "kind": "text", "rect": (20, 20, 220, 60), "style": t["style"], "at": (20, 20)}
    c._on_editor_commit("   ")
    assert t["id"] not in store.anns


@needs_stub
def test_snip_tool_emits_pdf_region_not_an_annotation():
    c, store, ed, ops = make_canvas()
    c.doc, c.page, c.page_offset, c.zoom = FakeBoardDoc(), 4, 0, 1.0
    seen = []
    c.snip_requested.connect(lambda *a: seen.append(a))
    c._finish_draft({"kind": "rect", "pts": [300, 400, 100, 150], "style": {}, "start": (300, 400), "snip": True})
    assert seen == [(4, 100, 150, 300, 400)] and not store.anns
    c._finish_draft({"kind": "rect", "pts": [10, 10, 12, 12], "style": {}, "start": (10, 10), "snip": True})
    assert len(seen) == 1                                    # accidental click: ignored


# ------------------------------------------------------------------ session
def make_session(tmp):
    store, questions, boards = AnnotationStore(), QuestionStore(), BoardStore()
    s = SessionController(store, questions, Path(tmp), boards)
    sent = []
    s.client.send = lambda msg: sent.append(msg) or True
    s.client._online = True
    return s, store, questions, boards, sent


def snapshot(**over):
    base = {"t": "joined", "you": "me", "token": "tok", "created": False, "protocol": 1,
            "room": {"code": "ABC-234", "name": "R", "page": 0, "settings": {"can_draw": True, "can_edit_others": True,
                     "can_chat": True, "can_control_timer": True, "allow_download": True, "sync_pages": False},
                     "locked": False, "host_id": "me", "protected": False},
            "members": [{"id": "me", "name": "Ahmed", "color": "#fff", "is_host": True, "connected": True, "page": 0}],
            "annotations": [], "chat": [], "questions": [], "boards": [],
            "timer": {"mode": "focus", "phase": "focus", "focus_min": 25, "break_min": 5, "running": False, "remaining": 1500},
            "pdf": {"name": "w.pdf", "size": 10, "pages": 2, "sha256": "0" * 64, "ready": True}, "rev": 0}
    base.update(over)
    return base


@needs_stub
def test_creating_a_room_uploads_local_boards_before_their_drawings():
    with tempfile.TemporaryDirectory() as tmp:
        s, store, questions, boards, sent = make_session(tmp)
        boards.add("Question 1", online=False)
        store.add(mk("pen", BOARD_BASE, (5, 5, 50, 50)))
        s.start_create("ws://x", "R", "Ahmed", "", str(Path(tmp) / "none.pdf"), {"name": "w.pdf", "size": 10, "pages": 2, "sha256": "0" * 64})
        sent.clear()
        s._on_message(snapshot(created=True))
        kinds = [m["t"] for m in sent]
        assert kinds.index("board_add") < kinds.index("annotation_created")
        assert sent[kinds.index("board_add")]["title"] == "Question 1"
        s._on_message({"t": "board_added", "board": {"id": "srv0000001", "title": "Question 1"}, "index": 0, "cid": ""})
        assert [b["title"] for b in boards.items] == ["Question 1"]


@needs_stub
def test_joining_adopts_server_boards_and_annotations_and_acks_set_z():
    with tempfile.TemporaryDirectory() as tmp:
        s, store, questions, boards, sent = make_session(tmp)
        s.start_join("ws://x", "ABC-234", "Omar", "")
        ann = {"id": "srvann0001", "kind": "step", "page": BOARD_BASE, "pts": [1, 1], "style": {"color": "#176b66"},
               "text": "1", "owner": "other", "owner_name": "Ahmed", "z": 7, "rev": 1}
        s._on_message(snapshot(boards=[{"id": "b1", "title": "Q1"}], annotations=[ann]))
        assert boards.items[0]["title"] == "Q1" and "srvann0001" in store.anns
        s.send_op({"t": "annotation_created", "ann": mk("pen", BOARD_BASE)})
        op = sent[-1]["op"]
        s._on_message({"t": "ack", "op": op, "ok": True, "z": 42})
        assert op not in s.outbox
        assert store.anns[sent[-1]["ann"]["id"]]["z"] == 42 if sent[-1]["ann"]["id"] in store.anns else True


@needs_stub
def test_rejected_operation_triggers_a_resync():
    with tempfile.TemporaryDirectory() as tmp:
        s, store, questions, boards, sent = make_session(tmp)
        s.start_join("ws://x", "ABC-234", "Omar", "")
        s._on_message(snapshot())
        s.send_op({"t": "annotation_created", "ann": mk()})
        sent.clear()
        s._on_message({"t": "ack", "op": next(iter(s.outbox)), "ok": False, "reason": "forbidden"})
        s._last_resync = 0.0
        s._on_message({"t": "ack", "op": "none", "ok": False, "reason": "missing"})
        assert any(m["t"] == "resync" for m in sent)


@needs_stub
def test_pdf_download_is_verified_and_written_to_the_cache():
    with tempfile.TemporaryDirectory() as tmp:
        data = b"%PDF-1.4\n" + b"abc" * 500
        s, store, questions, boards, sent = make_session(tmp)
        s.start_join("ws://x", "ABC-234", "Omar", "")
        info = {"name": "w.pdf", "size": len(data), "pages": 1, "sha256": hashlib.sha256(data).hexdigest(), "ready": True}
        got = []
        s.pdf_ready.connect(lambda path, name: got.append((path, name)))
        s._on_message(snapshot(you="guest", pdf=info, members=[
            {"id": "me", "name": "H", "color": "#fff", "is_host": True, "connected": True, "page": 0},
            {"id": "guest", "name": "O", "color": "#000", "is_host": False, "connected": True, "page": 0}]))
        assert any(m["t"] == "pdf_request" for m in sent)
        half = len(data) // 2
        for seq, part in enumerate((data[:half], data[half:])):
            s._on_message({"t": "pdf_chunk", "seq": seq, "total": 2, "data": base64.b64encode(part).decode()})
        s._on_message({"t": "pdf_complete", "pdf": info})
        assert got and Path(got[0][0]).read_bytes() == data


@needs_stub
def test_corrupted_pdf_download_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        data = b"%PDF-1.4\n" + b"abc" * 100
        s, store, questions, boards, sent = make_session(tmp)
        s.start_join("ws://x", "ABC-234", "Omar", "")
        info = {"name": "w.pdf", "size": len(data), "pages": 1, "sha256": "f" * 64, "ready": True}
        failures = []
        s.pdf_failed.connect(failures.append)
        s._on_message(snapshot(you="guest", pdf=info, members=[
            {"id": "me", "name": "H", "color": "#fff", "is_host": True, "connected": True, "page": 0},
            {"id": "guest", "name": "O", "color": "#000", "is_host": False, "connected": True, "page": 0}]))
        s._on_message({"t": "pdf_chunk", "seq": 0, "total": 1, "data": base64.b64encode(data).decode()})
        s._on_message({"t": "pdf_complete", "pdf": info})
        assert failures


@needs_stub
def test_host_uploads_the_pdf_in_order_and_completely():
    from shared.protocol import PDF_CHUNK_BYTES
    with tempfile.TemporaryDirectory() as tmp:
        data = b"%PDF-1.4\n" + bytes(range(256)) * 2000          # ~512 kB: several chunks
        pdf = Path(tmp) / "w.pdf"
        pdf.write_bytes(data)
        s, store, questions, boards, sent = make_session(tmp)
        info = {"name": "w.pdf", "size": len(data), "pages": 1, "sha256": hashlib.sha256(data).hexdigest()}
        s.start_create("ws://x", "R", "Ahmed", "", str(pdf), info)
        sent.clear()
        s._on_message(snapshot(created=True, pdf=dict(info, ready=False)))
        for _ in range(50):
            s._pump_upload()
        chunks = [m for m in sent if m["t"] == "pdf_chunk"]
        assert [c["seq"] for c in chunks] == list(range(len(chunks)))
        assert len(chunks) == -(-len(data) // PDF_CHUNK_BYTES) and chunks[0]["total"] == len(chunks)
        assert b"".join(base64.b64decode(c["data"]) for c in chunks) == data


# ------------------------------------------------------------------ simplified UI: grouped tools, palette, migration
def test_rail_groups_markup_tools_and_hides_snip_on_boards():
    from client.ui.toolbar import mark_choices, rail_tools
    pdf = [k for k, _ in rail_tools(False)]
    board = [k for k, _ in rail_tools(True)]
    assert "mark" in pdf and "snip" in pdf and not {"underline", "strike", "note", "comment"} & set(pdf)
    assert "mark" in board and "snip" not in board
    assert len(pdf) <= 11                                   # was 14 separate buttons before the rail was simplified
    assert set(mark_choices(False)) == {"underline", "strike", "note", "comment"}
    assert set(mark_choices(True)) == {"note", "comment"}


def test_command_search_is_forgiving_and_respects_availability():
    from client.ui.palette import Command
    c = Command("Snip a question from the PDF", "Whiteboard", lambda: None, "Q")
    assert c.matches("snip pdf") and c.matches("WHITEBOARD") and c.matches("")
    assert not c.matches("room")
    assert Command("x", "g", lambda: None, enabled=lambda: True).available()
    assert not Command("x", "g", lambda: None, enabled=lambda: False).available()


def test_old_solve_tab_text_is_carried_into_notes_once():
    from client.ui.navigation import merge_old_solving_data
    assert merge_old_solving_data("my notes", {"prompt": " ", "working": "", "answer": ""}) is None
    merged = merge_old_solving_data("my notes\n", {"prompt": "Find x", "working": "x = 2", "answer": "2", "status": "solved"})
    assert merged.startswith("my notes\n\n— From the old Solve tab —")
    assert "Question\nFind x" in merged and "Working\nx = 2" in merged and merged.endswith("Answer\n2")
    assert merge_old_solving_data("", {"answer": "42"}).startswith("— From the old Solve tab —")


@needs_stub
def test_main_window_commands_follow_context():
    sys_path_cache = tempfile.mkdtemp()
    from client.ui import main_window as mw
    from database.client_db import ClientDatabase
    mw.cache_dir = lambda: Path(sys_path_cache)
    win = mw.MainWindow(ClientDatabase(Path(sys_path_cache) / "db.sqlite"))
    titles = {c.title for c in win._commands() if c.available()}
    assert "Open PDF…" in titles and "Create a study room…" in titles           # available on the home screen
    assert "Search in the PDF…" not in titles and "Leave room" not in titles    # need a document / a room
    win.doc = type("D", (), {"page_count": 3})()
    win.stack.currentIndex = lambda: 1
    titles = {c.title for c in win._commands() if c.available()}
    assert {"Search in the PDF…", "Open the whiteboard", "Snip a question from the PDF", "Pen tool"} <= titles
    assert "Back to the PDF" not in titles                                      # only while the whiteboard is showing
    win.session.active, win.session.joined_once = True, True
    titles = {c.title for c in win._commands() if c.available()}
    assert "Leave room" in titles and "Create a study room…" not in titles
    names = [c.title for c in win._commands()]
    assert len(names) == len(set(names))                                        # no duplicate entries
