"""Logic tests for validation, permissions, conflict handling and the timer (no network needed)."""
import time

from server.models.room import Room
from shared.protocol import ValidationError, clean_text, normalize_room_code, validate_annotation
from shared.timer import TimerState


def ann(i="abcdef01", **kw):
    base = {"id": i, "kind": "pen", "page": 0, "pts": [1, 2, 3, 4], "style": {"color": "#ff0000", "width": 2}}
    base.update(kw)
    return base


def make_room():
    r = Room("ABC-492", "Test")
    r.pdf.update(pages=3, ready=True)
    host, _ = r.add_member("Host", host=True)
    guest, _ = r.add_member("Guest")
    return r, host, guest


def test_room_code():
    assert normalize_room_code("abc492") == "ABC-492"
    assert normalize_room_code("bad") == ""


def test_validation_rejects_garbage():
    for bad in (ann(kind="evil"), ann(pts=[1]), ann(pts=[float("inf"), 1]), ann(id="x"), ann(page=-1),
                ann(style={"color": "red"})):
        try:
            validate_annotation(bad)
        except ValidationError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_chat_sanitised():
    assert clean_text("hi\x00 there\u202e", 100) == "hi there"
    assert len(clean_text("x" * 5000, 1000)) == 1000


def test_delete_wins_over_concurrent_move():
    r, host, guest = make_room()
    assert r.apply_create(host, ann()).ok
    assert r.apply_delete(guest, "abcdef01").ok                     # Mohamed deletes
    res = r.apply_update(host, "abcdef01", {"pts": [5, 6, 7, 8]})   # Ahmed's move arrives late
    assert not res.ok and res.reason == "missing"
    assert not r.apply_create(host, ann()).ok                       # no resurrection after delete


def test_last_writer_wins_per_field():
    r, host, guest = make_room()
    r.apply_create(host, ann("text0001", kind="text", pts=[0, 0, 100, 20], text="a"))
    r.apply_update(host, "text0001", {"pts": [10, 10, 110, 30]})
    r.apply_update(guest, "text0001", {"text": "b"})
    a = r.annotations["text0001"]
    assert a["pts"] == [10, 10, 110, 30] and a["text"] == "b" and a["rev"] == 3


def test_create_is_idempotent_for_owner_only():
    r, host, guest = make_room()
    assert r.apply_create(guest, ann()).ok
    assert r.apply_create(guest, ann()).dup
    assert not r.apply_create(host, ann()).ok or r.apply_create(host, ann()).dup is True


def test_permissions_enforced_server_side():
    r, host, guest = make_room()
    r.settings["can_edit_others"] = False
    r.apply_create(host, ann())
    assert r.apply_update(guest, "abcdef01", {"pts": [1, 1]}).reason == "forbidden"
    assert r.apply_delete(guest, "abcdef01").reason == "forbidden"
    r.settings["can_draw"] = False
    assert r.apply_create(guest, ann("zzzzzz01")).reason == "forbidden"
    assert r.apply_create(host, ann("zzzzzz02")).ok                 # host always may


def test_token_auth():
    r, host, _ = make_room()
    m, token = r.add_member("Omar")
    assert r.member_by_token(token) is m and r.member_by_token("nope") is None


def test_password():
    r = Room("XYZ-234", "Pw", password="secret")
    assert r.check_password("secret") and not r.check_password("nope") and not r.check_password(None)


def test_timer_pomodoro_cycle():
    t = TimerState()
    t.configure("pomodoro", 25, 5)
    t.start(100.0)
    assert round(t.now_remaining(160.0)) == 25 * 60 - 60
    t.pause(160.0)
    assert not t.running and round(t.remaining) == 24 * 60
    t.start(200.0)
    t.finish(200.0 + 24 * 60)
    assert t.phase == "break" and t.running and t.remaining == 300


def test_large_pdf_chunks_fit_websocket_frame():
    import base64
    import json
    from shared.protocol import MAX_FRAME_BYTES, PDF_CHUNK_BYTES

    # Base64 expansion plus the JSON envelope must remain under the server's frame cap.
    payload = json.dumps({
        "t": "pdf_chunk", "seq": 0, "total": 1,
        "data": base64.b64encode(b"x" * PDF_CHUNK_BYTES).decode("ascii"),
    }, separators=(",", ":"))
    assert PDF_CHUNK_BYTES == 512 * 1024
    assert len(payload) < MAX_FRAME_BYTES


def test_annotation_opacity_round_trips_for_collaboration():
    item = validate_annotation(ann(style={"color": "#ff0000", "width": 2, "opacity": 0.45}))
    assert item["style"]["opacity"] == 0.45
    for opacity in (0, 1.1, float("inf")):
        try:
            validate_annotation(ann(style={"color": "#ff0000", "width": 2, "opacity": opacity}))
        except ValidationError:
            continue
        raise AssertionError(f"accepted opacity {opacity}")


# ---- whiteboards, question snips and step markers --------------------------------------------
import base64

from shared.protocol import BOARD_BASE, MAX_BOARDS


def _png_b64(n=300):
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * n).decode()


def test_board_pages_need_an_existing_board():
    r, host, guest = make_room()
    board_ann = ann("board001", page=BOARD_BASE)
    assert r.apply_create(guest, board_ann).reason == "invalid"          # no board yet
    assert r.add_board(guest, "Question 1")["title"] == "Question 1"
    assert r.apply_create(guest, board_ann).ok
    assert r.apply_create(guest, ann("board002", page=BOARD_BASE + 1)).reason == "invalid"


def test_board_limit_and_permissions():
    r, host, guest = make_room()
    for i in range(MAX_BOARDS):
        assert r.add_board(host, f"B{i}")
    assert r.add_board(host, "one too many") is None
    r2, host2, guest2 = make_room()
    r2.settings["can_draw"] = False
    assert r2.add_board(guest2, "nope") is None and r2.add_board(host2, "ok")


def test_question_snip_image_validated():
    r, host, guest = make_room()
    r.add_board(host, "Q")
    img = {"id": "image001", "kind": "image", "page": BOARD_BASE, "pts": [10, 10, 300, 200], "style": {"color": "#000000"}}
    assert not r.apply_create(host, dict(img)).ok                         # no data
    assert not r.apply_create(host, dict(img, data="not-an-image-at-all" * 20)).ok
    assert not r.apply_create(host, dict(img, data=base64.b64encode(b"GIF89a" + b"x" * 300).decode())).ok
    assert not r.apply_create(host, dict(img, data="iVBORw0KGgo" + "A" * 800_000)).ok       # too big
    assert r.apply_create(host, dict(img, data=_png_b64())).ok


def test_step_marker():
    r, host, _ = make_room()
    r.add_board(host, "Q")
    step = {"id": "step0001", "kind": "step", "page": BOARD_BASE, "pts": [10, 10], "style": {"color": "#176b66"}, "text": "1"}
    assert r.apply_create(host, dict(step)).ok
    assert not r.apply_create(host, dict(step, id="step0002", text="")).ok
    assert not r.apply_create(host, dict(step, id="step0003", pts=[1, 2, 3, 4])).ok


def test_boards_survive_snapshot_and_reset():
    r, host, _ = make_room()
    r.add_board(host, "Q1")
    assert r.snapshot()["boards"][0]["title"] == "Q1"
    r.reset_document()
    assert r.snapshot()["boards"] == []
