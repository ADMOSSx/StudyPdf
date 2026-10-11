# StudyPDF

Solve PDF worksheets together, live. PDF reader and annotator with a shared live study room.

Desktop client: **Python + PySide6 (Qt)** · PDF engine: **PyMuPDF** · Server: **asyncio + websockets** · Storage: **SQLite** · Version: **2.5.0**

## Quick start (Windows, Python 3.10+)

```powershell
cd StudyPDF
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# terminal 1 - the collaboration server
python run.py server --host 0.0.0.0 --port 8765

# terminal 2 - the desktop app (as many copies as you like)
python run.py
```

Offline mode needs no server: *Open PDF* works as a normal reader/annotator. The interface uses the light **Paper** academic theme only. Use `Ctrl+F` to search text across a PDF in a background worker, `Ctrl+G` to jump directly to a page, and `Ctrl+B` to bookmark/unbookmark the current page. The workspace has **Live Study Together** room actions, a full-size, **live-synchronised Whiteboard** (see below), and a **Solve** tab for the problem statement, working/reasoning, final answer and progress status, a **Hand / Pan** tool (`G`) for dragging the page, and a button to hide or restore the right collaboration panel. Whiteboards are shared with everyone in the room in real time and saved with the document. `V` returns to Select. The navigation sidebar includes filtered page thumbnails, PDF outline, and saved pages. Personal notes save per document on this device.

### Creating a room / joining from another PC
1. **Create Study Room** → pick a name, the PDF, and the server address (`ws://localhost:8765` on the same PC) → *Create Room*.
   The "Room Created!" card shows the code (e.g. `ABC-492`) and uploads the PDF to the server once.
2. Friends click **Join Study Room**, enter the code, their name and the **same server address**.
3. Friends on other networks need to reach your server:
   * same Wi-Fi/LAN: use `ws://<your-LAN-IP>:8765` (allow port 8765 in Windows Firewall);
   * over the internet: forward TCP 8765 on your router, or use a tunnel (Tailscale, `cloudflared tunnel --url http://localhost:8765`, ngrok) and use the `wss://` address it gives you;
   * for public hosting run the server on a VPS, ideally behind Caddy/nginx for TLS, or pass `--cert/--key` to serve `wss://` directly.

## Keyboard
New in 2.4: `Q` snip a question, `M` step marker, `Ctrl+Shift+W` whiteboard.

V select · G hand/pan · P pen · H highlighter · T text · R/O/A/L shapes · U underline · S strike · N note · C comment · E eraser ·
Ctrl+Z / Ctrl+Y undo/redo · Ctrl+wheel zoom · PgUp/PgDn pages · Ctrl+S save annotated copy · F11 reading mode · Del delete selection.

## Architecture

```
client/ui              Qt widgets: home, workspace, canvas, panels, dialogs, theme, vector icons
client/pdf             engine.py (PyMuPDF render/words) · exporter.py (annotated + flattened export)
client/annotations     model.py (data + geometry + store) · render.py (QPainter) · editor.py (undo/redo + ops)
client/collaboration   net.py (QWebSocket, reconnect) · session.py (protocol state machine) · questions.py
server/websocket       handler.py: auth, validation, rate limits, dispatch, broadcast
server/rooms           manager.py: registry, expiry, timers
server/models          room.py: ALL rules (permissions, conflict handling) - pure, unit-tested
shared                 protocol.py (validators, constants) · timer.py
database               server_db.py (rooms survive restarts) · client_db.py (recent docs, autosaved sessions)
```

**PDF rendering and memory.** The main page raster is rendered by a small background thread pool using an independent PyMuPDF document handle, so rasterisation itself does not hold up the UI thread. The canvas shows a lightweight white page until the raster arrives. Document fingerprinting and some PDF metadata setup still happen during opening. PyMuPDF rasterises only the visible page at `zoom × devicePixelRatio`; a page raster is capped at ~10 MP and the UI keeps an LRU of up to 3 renders with an ~8 MP aggregate cache target. Word boxes for underline/strike snapping use an 8-page LRU instead of growing for every page visited. Annotations are *never* baked into the page raster: they are painted as vectors on top, so drawing and remote edits don't re-render the PDF. Thumbnails render lazily. Huge scanned pages can still take time to rasterise, but the app limits avoidable memory spikes.

**Annotation representation.** Plain JSON dicts: `{id, kind, page, pts:[x,y,...], style:{color,width,size}, text?, owner, z, rev}`.
Coordinates are PDF points (top-left origin), so zoom/resolution never affects positions. Kinds: pen, highlighter, line,
arrow, rect, ellipse, underline, strike, text, note, comment. One flat `pts` list makes move/resize a generic affine map.

**Protocol (JSON over WebSocket).** Client→server: `create_room`, `join_room`, `annotation_created/_updated/_deleted`
(each with an op id), `live_stroke`, `cursor_moved`, `page_changed`, `chat`, `question_set/_delete`, `timer`, `pdf_chunk`,
`pdf_request`, `resync`, host actions `kick`, `set_settings`, `clear_annotations`, `reset_document`, `end_session`.
Server→clients: the same events broadcast to the others, plus `ack{op,ok,reason,z}`, `joined/snapshot` (full state:
PDF info, annotations, members, chat, questions, timer, page, settings), `user_joined/user_left`, `timer_state`,
`settings_changed`, `kicked`, `session_ended`, `error{code}`. The PDF itself is sent once, in 512 KiB base64 chunks, verified by SHA-256. Upload bursts send four chunks per Qt timer tick to reduce per-message overhead while keeping the UI responsive. Client transfers are streamed from/to disk to avoid loading the entire PDF into RAM; collaborative PDFs up to 500 MB are supported subject to available disk space and server configuration.
Drawing is streamed live (`live_stroke`, ~16 Hz) so friends see strokes appear while you draw; the final `annotation_created` replaces the preview.

**Rooms.** Random codes like `ABC-492` (24 letters × 8 digits, collision re-rolled), optional scrypt-hashed password, member
tokens (stored hashed) for reconnecting as the same person/host, lock, kick, TTL expiry (72 h idle), rooms and PDFs persisted in SQLite/disk.

**Staying in sync + conflicts.** The server is the single source of truth and processes messages one at a time (single asyncio loop),
so arrival order *is* the ordering. Clients apply edits optimistically, send them with an op id, and the server
broadcasts accepted ops to everybody else. Rules (see `server/models/room.py`, covered by `tests/`):
* **Delete wins.** Deleted ids are tombstoned; a late move/edit gets `ack{ok:false, reason:"missing"}` and a late re-create is refused. (Ahmed moves while Mohamed deletes → it's deleted for everyone, nothing crashes.)
* **Field-level last-writer-wins.** `pts`, `text`, `style` are merged independently, so a move and a text edit made at the same time both survive.
* **Idempotency.** Re-sending the same create (same owner) is a no-op, so retries after a reconnect are safe.
* **Rejected ops** make the client request a full `resync`.
* **Undo is per-user** and never rewinds other people's work; undoing a delete re-creates under a new id (the server remembers the old one as deleted).
* **Reconnect:** exponential back-off, then `join_room` with the token → fresh snapshot → the client replays its un-acknowledged ops on top. Banner: "Connection lost… Reconnecting…" → "Connected ✓ Syncing changes…".

**Security.** Server-side permission checks on every op (host-only actions are checked by member id from the token, never by a client flag);
strict schema validation (kinds, finite bounded numbers, colour regex, id regex, size limits); chat/names/text stripped of control and bidi-override
characters and always rendered as escaped plain text; per-connection token-bucket rate limit; failed-join throttling per IP; rooms-per-IP limit; 15 s auth timeout;
bounded outbound queues (a slow client is dropped, never stalls the room). *Limit:* "disallow downloads" hides export in the UI, but every member necessarily holds the PDF to view it, so it cannot be a hard guarantee.
Kicked users can rejoin with the code unless the host locks the room (or uses a password).

## Version 2.5.0 - simpler, cooler interface

**Less clutter**
- The menu bar is gone. Everything it did is now in one searchable **command palette** (`Ctrl+K`, works on every screen) plus
  the buttons and shortcuts you already use.
- The top bar went from eight unlabeled icons to a clear layout: sidebar toggle, brand, a **PDF | Whiteboard** switch in the
  middle, then *Search*, *Save & export*, **Study together** (becomes *Invite friends* inside a room), *More* and the right-panel toggle.
- The tool rail dropped from 14 buttons to 10: **Underline, Strike-through, Sticky note and Comment** share one *Mark up* menu.
- The right panel shows four tabs (Chat, People, Questions, Notes) instead of six. The Solve tab was redundant with the
  whiteboard, so it was removed; anything you had written there is moved into your Notes for that PDF automatically, once.
- Removed duplicate helper text on the home screen and in the whiteboard header (the top-bar switch replaces its back button).

**Cooler look and feel**
- Floating controls (tool rail, style bar, page/zoom pill) now cast a soft shadow so they hover above the page.
- Pill-shaped PDF | Whiteboard switch, a compact timer ring, tighter tabs.
- The window remembers its size and position.

**Shortcuts**: `Ctrl+K` command palette, `Ctrl+F` search, `Ctrl+B` bookmark, `Ctrl+G` go to page, `Ctrl+O` open,
`Ctrl+Shift+W` whiteboard, `Q` snip, `M` step marker, `F11` reading mode.

## Version 2.4.0 - collaborative whiteboard for solving questions

The whiteboard is now built on the same annotation engine as the PDF, so it is synchronised in rooms (live strokes,
named cursors, undo, permissions, reconnect) and saved/restored with the document.

**How to solve a question together**
1. In the PDF, pick the **Snip** tool (`Q`, scissors icon in the tool rail) and drag a box around the question. Give the board a
   title; the cropped question lands on a new whiteboard (and optionally in your Questions list). Or open the whiteboard
   (`Ctrl+Shift+W`) and use **Question & solution → Add question text...** (also reachable from the `Ctrl+K` palette).
2. **Add solution template** drops *Given / Find / Steps / Answer* headings (one undo step).
3. Work it out with pen, shapes, text, sticky notes and comments. **Step marker** (`M`) numbers your solution steps 1, 2, 3...
   The **Σ** menu (text tools) inserts maths symbols (x², √, π, ≤, ∑, ∫, ⇒ ...), fractions and square roots.
4. Several boards per room (up to 20), tabs along the top, grid / ruled / dots / blank backgrounds, **Export** as PNG or one PDF.

Technical notes: board *i* uses annotation page `10000 + i`; boards are append-only and ordered by the server (`board_add` is
echoed to everybody, so all clients agree on indexes even if two people add one at once). Question snips are PNG/JPEG
(<= 700 kB, magic-byte validated) stored as `image` annotations; the eraser never removes them (select + Delete does).

**Fixes and improvements in 2.4.0**
- Pages no longer flash white while zooming or flipping: the previous raster is shown (slightly soft) until the sharp one arrives.
- The page thumbnail list shared one placeholder instead of allocating a pixmap per page (hundreds of MB on a 5000-page book).
- Clearing all the text of a text box / note now deletes it instead of silently keeping the old text.
- Opening the maths-symbol menu no longer commits and closes the text you are typing.
- Default text colour on the PDF is dark (it used to be near-white and invisible on a white page); the colour swatches no longer
  include an invisible white.
- Rooms restore their boards after a server restart; the host's local boards are uploaded when creating a room.
- New logic tests (`tests/test_client_logic.py`) run against real PySide6 or a tiny built-in stand-in (`tests/qtstub.py`).

## Version 2.3.3 whiteboard and academic workspace updates

- **Live Study Together** is named clearly in the workspace toolbar and offers create/join room actions. The existing collaboration server, room codes, live strokes, cursors, chat and shared questions power the session.
- Removed the separate Solve Mode toggle; the PDF remains the primary solving surface at all times.
- (Superseded in 2.5.0: the Solve tab was merged into Notes and the whiteboard.)
- Standardized the app on the light **Paper** theme; dark mode and its toggle have been removed.
- Added a dedicated **Whiteboard** tab with pen, highlighter, eraser, color and stroke-width controls, undo, clear confirmation, and PNG export. Drawings autosave locally per PDF.
- **Hand / Pan** (`G`) lets users drag the page with the left mouse button, including room members who are view-only. `V` returns to Select.
- **Hide right panel** collapses chat/people/questions/notes and restores them with the same active tab intact.

## Version 2.3 performance and completion notes

- Main-page raster rendering now runs asynchronously in a bounded two-worker pool. Each task opens its own PyMuPDF document handle rather than sharing the UI document across threads.
- The canvas displays a white placeholder while the raster is generated, then replaces it with the rendered page without blocking navigation or annotation input.
- Render results are ignored if they belong to a document that has since been closed or replaced. The existing 10 MP per-raster cap and bounded page cache remain in place.
- The core four milestones are included in this project: bounded PDF rendering, annotation/export tools, collaborative study rooms, and Windows packaging instructions. Network collaboration still requires a reachable server; a ZIP alone does not host a public relay.

## Features checklist
Open/drag-drop PDF, lazy thumbnails, background full-document text search (Ctrl+F), per-document bookmarks (Ctrl+B), page jump/prev/next, zoom, fit page/width, reading mode, persistent light theme · pen, highlighter, object eraser, text, rectangle, circle, arrow, line,
underline & strike (snap to the words you drag over), comments, sticky notes, select/move/resize/delete, undo/redo · rooms, presence with colours and live named cursors,
chat, shared study timer (+Pomodoro), question tracker (Solved / In progress / Difficult) · permissions & host tools · autosave + restore, session files (`.studypdf`),
annotated-copy export (vector, selectable text, comments as real PDF comments) and flattened export (never overwrites the original) · reconnect, friendly errors.

Eraser removes whole annotations (not pixels). Questions are shared in rooms and local offline. Text uses Qt rendering, so Arabic/any script works in exports.

## Tests
`pip install -r requirements-dev.txt && python -m pytest -q` - validation, permissions, conflict rules, timer, a full create→join→sync→kick flow over in-memory sockets, and a check that the 512 KiB transfer chunk fits the WebSocket frame limit.

## Packaging for Windows
```powershell
pip install pyinstaller
pyinstaller --noconfirm --windowed --name StudyPDF --collect-all PySide6 run.py     # -> dist\StudyPDF\StudyPDF.exe
pyinstaller --noconfirm --onefile --name StudyPDFServer run_server.py                # -> dist\StudyPDFServer.exe
```
Zip `dist\StudyPDF` for friends (or wrap it with Inno Setup). Run the server exe on the host machine / VPS: `StudyPDFServer.exe --port 8765`.

## Server options
`--host --port --data-dir server_data --cert --key --room-ttl-hours`. Data (SQLite + PDFs) lives in `server_data/`.

## StudyPDF 2.1 additions

- Light and dark themes are switched at runtime and persisted in local settings. The theme menu is available from the home screen as well as the workspace.
- PDF text search runs in a dedicated worker with a separate PyMuPDF document handle, so searching a large text-based PDF does not block page navigation. Results are capped at 1,000 matches for predictable memory use. Scanned PDFs need OCR text to be searchable.
- **Smart navigation for long books:** filter page thumbnails by page number, jump directly with `Ctrl+G`, use the PDF's built-in outline/bookmarks, or search the document with `Ctrl+F`. Thumbnails remain lazy and are rendered only when visible, even for 700+ pages.
- **Professional annotation controls:** color presets/custom color, stroke/text sizing, undo/redo, plus an opacity slider. Opacity is part of the annotation data and is validated and synchronized to other room members.
- **Study tools:** personal notes save automatically per PDF on this device; the room workspace keeps shared chat, member presence/live cursors, questions, shared timer/Pomodoro and collaborative annotations.
- The collaboration engine includes room codes, permissions, live strokes/cursors, chat, questions, a shared timer, autosave, PDF transfer verification, reconnect/resync and server-side conflict checks.

## Large PDF performance notes

- Page thumbnails are rendered only when visible in the thumbnail panel; scrolling schedules the next visible thumbnails instead of rendering every page in the background.
- The page raster cache is bounded (up to 3 renders with an ~8 MP aggregate target), and individual page renders are capped at about 10 MP to reduce memory spikes.
- Large PDFs can still take time to open if they contain very high-resolution scans, complex vectors, or if the app must calculate a document fingerprint.


## StudyPDF 2.2 additions

- **Faster long-book navigation:** the page filter understands exact page numbers (`500` or `page 500`), ranges (`100-120`), and lists (`1, 3, 7-9`). It filters the existing page rows without forcing off-screen thumbnail rendering. `Ctrl+G` remains the direct-jump shortcut; outline, saved bookmarks and `Ctrl+F` text search provide additional routes through large PDFs.
- **Professional annotation presets:** the annotation properties bar now offers quick Fine / Standard / Bold presets, dedicated Soft / Classic / Bold marker presets, and Small / Standard / Large text presets. Colour, width and opacity remain adjustable manually; undo/redo and vector annotations are retained.
- **Study workspace:** personal notes are saved per PDF, while the question tracker, Pomodoro timer, chat, people/presence and live shared annotations are available in a study room. Host controls include room settings and member removal.
- **Long-document-friendly defaults:** thumbnails remain lazy, page rasters are bounded, and PDF text search uses a worker thread. Performance still depends on PDF page complexity and disk/CPU speed.
"# StudyPdf" 
