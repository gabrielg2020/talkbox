"""Read-along playback: highlight each word as Kokoro speaks it.

Playback is driven by mpv (libmpv via python-mpv), which lets us change speed
live and pitch-corrected. Word timings and mpv's ``time_pos`` are both in media
time, so the highlight stays in sync at any speed with no extra maths.

Single-threaded input — keys are polled with select() under cbreak mode rather
than a background reader, so nothing lingers to swallow keystrokes once playback
ends and the menu comes back.
"""

import bisect
import os
import select
import sys
import termios
import threading
import time
import tty

from rich.align import Align
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

FPS = 30
SPEED_MIN, SPEED_MAX, SPEED_STEP = 0.5, 2.5, 0.1
SEEK_STEP = 10  # seconds for ←/→

_BULLET = "  • "
# Heading colours echo the app's warm→cool gradient; p/li fall back to white.
_KIND_STYLE = {
    "h1": "bold #ff5faf",
    "h2": "bold #af5fff",
    "h3": "bold #5fd7ff",
}


def _li_line_start(words, i, first):
    """True if word i begins a list item's line (so it gets a bullet)."""
    return words[i].kind == "li" and (i == first or "\n" in words[i - 1].ws)


def _page_end(words, start, console):
    """Index one past the last word that fits on a page starting at ``start``.

    Measures real wrapped-line widths so a page holds exactly what the box can
    show. The text stays put within a page; we only ever advance by whole pages,
    so words never drift under the reader's eyes mid-read.
    """
    interior_w = max(10, console.size.width - 8)    # borders + horizontal padding
    interior_h = max(2, console.size.height - 4)    # borders + vertical padding
    i, line, col = start, 1, 0
    while i < len(words):
        w = words[i]
        if _li_line_start(words, i, start):
            col += len(_BULLET)
        if col > 0 and col + len(w.text) > interior_w:  # soft wrap
            line += 1
            col = 0
        if line > interior_h:
            return i
        col += len(w.text)
        for c in w.ws:  # ws may carry the newlines that end blocks
            if c == "\n":
                line += 1
                col = 0
            else:
                col += 1
        i += 1
    return i


def _line_starts(words, console):
    """Word index that begins each content line (same wrapping as `_page_end`),
    deduped so blank block-separator lines don't repeat an index. Used to scroll
    by line; recompute on a width change."""
    interior_w = max(10, console.size.width - 8)
    starts, col = [0], 0
    for i, w in enumerate(words):
        if _li_line_start(words, i, 0):
            col += len(_BULLET)
        if col > 0 and col + len(w.text) > interior_w and starts[-1] != i:  # soft wrap
            starts.append(i)
            col = 0
        col += len(w.text)
        for c in w.ws:
            if c == "\n":  # newline → next word begins a line
                if i + 1 < len(words) and starts[-1] != i + 1:
                    starts.append(i + 1)
                col = 0
            else:
                col += 1
    return starts


def _classify_key(data):
    """Map a raw key byte-sequence to a semantic event (or None to ignore).

    Arrow keys arrive as ``\\x1b[A``/``\\x1b[B`` (or ``\\x1bOA``/``\\x1bOB`` in
    application-cursor-key mode); a lone ``\\x1b`` is Esc.
    """
    return {
        b" ": "pause",
        b"q": "quit",
        b"\x1b[A": "faster",
        b"\x1bOA": "faster",
        b"\x1b[B": "slower",
        b"\x1bOB": "slower",
        b"\x1b[C": "forward",
        b"\x1bOC": "forward",
        b"\x1b[D": "back",
        b"\x1bOD": "back",
        b"]": "next_block",
        b"[": "prev_block",
        b"n": "search_next",
        b"N": "search_prev",
        b"k": "scroll_up",
        b"j": "scroll_down",
        b"\x1b[5~": "page_up",
        b"\x1b[6~": "page_down",
        b"f": "follow",
        b"+": "louder",
        b"=": "louder",  # the unshifted '+' key
        b"-": "softer",
        b"\x1b": "quit",
    }.get(data)


def _mouse_scroll(data):
    """Wheel direction from an SGR mouse report (ESC [ < b ; x ; y M), or None.
    Needs mouse reporting enabled; wheel up = button 64, down = 65."""
    if not data.startswith(b"\x1b[<"):
        return None
    try:
        button = int(data[3:].split(b";", 1)[0])
    except (ValueError, IndexError):
        return None
    return "scroll_up" if button == 64 else "scroll_down" if button == 65 else None


def _read_raw():
    """Return the next raw key burst (bytes) or None.

    Reads with raw os.read so select() (kernel buffer) and the read agree — a
    buffered sys.stdin.read would slurp an arrow's trailing bytes into userspace,
    hiding them from select() and making arrows look like a bare Esc. The whole
    escape burst is grabbed at once; if a lone Esc arrives split from its
    sequence, a brief second look picks up the rest.
    """
    fd = sys.stdin.fileno()
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    data = os.read(fd, 32)  # 32 is roomy enough for an SGR mouse report
    if data == b"\x1b" and select.select([sys.stdin], [], [], 0.02)[0]:
        data += os.read(fd, 8)
    return data


def _fmt_time(seconds):
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _build_sections(words):
    """For each word, the heading currently in effect (for the breadcrumb)."""
    out = [""] * len(words)
    current, i = "", 0
    while i < len(words):
        if words[i].kind in ("h1", "h2", "h3"):
            j = i
            while j < len(words) and words[j].kind == words[i].kind:
                j += 1
            current = " ".join(words[k].text for k in range(i, j)).strip()
            for k in range(i, j):
                out[k] = current
            i = j
        else:
            out[i] = current
            i += 1
    return out


def _block_starts(words):
    """Word indices that begin a block (start, or after a block-break newline)."""
    return [0] + [i for i in range(1, len(words)) if "\n" in words[i - 1].ws]


def _target_block(block_starts, idx, direction):
    """The block-start index one block before/after the current one, or None."""
    here = bisect.bisect_right(block_starts, max(idx, 0)) - 1
    target = here + direction
    return block_starts[target] if 0 <= target < len(block_starts) else None


def _seek_to_word(player, words, i):
    """Seek just inside word i, so the highlight resolves to i and not i-1.

    Seeking to exactly words[i].start can land time_pos a hair below it (float /
    seek skew), which bisect_right then reads as the previous word — leaving `n`
    stuck one short. A small in-word offset avoids that."""
    w = words[i]
    player.seek(w.start + min(0.05, (w.end - w.start) * 0.4), reference="absolute", precision="exact")


def _search(words, idx, query, direction):
    """Index of the next word containing query (case-insensitive), wrapping. None
    if no match. direction +1 forward, -1 backward."""
    q = query.lower()
    n = len(words)
    if not q or n == 0:
        return None
    for step in range(1, n + 1):
        i = (idx + step * direction) % n
        if q in words[i].text.lower():
            return i
    return None


def _parse_time(s):
    """'90' -> 90.0, '1:30' -> 90.0, '1:02:03' -> 3723.0; None if not a time."""
    if not s or any(c not in "0123456789:" for c in s):
        return None
    try:
        nums = [int(p) for p in s.split(":")]
    except ValueError:
        return None
    if len(nums) == 1:
        return float(nums[0])
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


def _parse_command(text, words, idx):
    """Parse a command string ('/x', '?x', ':cmd') into an action tuple."""
    if not text:
        return ("none",)
    kind, rest = text[0], text[1:].strip()
    if kind in "/?":
        if not rest:
            return ("message", "")
        direction = 1 if kind == "/" else -1
        m = _search(words, idx, rest, direction)
        return ("search", m, rest, direction) if m is not None else ("message", f"not found: {rest}")
    if rest in ("q", "quit"):
        return ("quit",)
    if rest in ("toc", "contents"):
        return ("mode", "toc")
    if rest in ("help", "h"):
        return ("mode", "help")
    if rest.startswith("speed"):
        try:
            return ("speed", float(rest.split()[1]))
        except (IndexError, ValueError):
            return ("message", "usage: :speed 1.5")
    if rest.startswith("vol"):
        try:
            return ("volume", float(rest.split()[1]))
        except (IndexError, ValueError):
            return ("message", "usage: :vol 80")
    t = _parse_time(rest)
    if t is not None:
        return ("seek", t)
    return ("message", f"?: {rest}")


def _headings(words):
    """List of (text, word_index, kind) for each heading block."""
    out, i = [], 0
    while i < len(words):
        if words[i].kind in ("h1", "h2", "h3"):
            j = i
            while j < len(words) and words[j].kind == words[i].kind:
                j += 1
            out.append((" ".join(words[k].text for k in range(i, j)).strip(), i, words[i].kind))
            i = j
        else:
            i += 1
    return out


def _current_heading(headings, idx):
    """Index into headings of the section the cursor is currently in."""
    sel = 0
    for n, (_, word_i, _) in enumerate(headings):
        if word_i <= idx:
            sel = n
        else:
            break
    return sel


def _command_bar(prefix, buffer):
    return f"[bold bright_cyan]{prefix}{buffer}▏[/bold bright_cyan]  [dim]enter: run · esc: cancel[/dim]"


def _toc_panel(headings, sel, console):
    text = Text()
    if not headings:
        text.append("(no headings in this document)", style="dim")
    else:
        interior_h = max(3, console.size.height - 4)
        lo = max(0, min(sel - interior_h // 2, len(headings) - interior_h))
        for i in range(lo, min(len(headings), lo + interior_h)):
            label, _, kind = headings[i]
            indent = {"h1": "", "h2": "  ", "h3": "    "}.get(kind, "")
            style = "bold black on bright_cyan" if i == sel else _KIND_STYLE.get(kind, "white")
            text.append(f"{indent}{label}\n", style=style)
    return Panel(
        Align(text, align="left", vertical="top"),
        title="[bold magenta]✦ contents ✦[/bold magenta]",
        subtitle="[dim]↑↓ move · enter: jump · esc: cancel[/dim]",
        border_style="bright_magenta", padding=(1, 3), height=console.size.height,
    )


_HELP = [
    ("space", "pause / resume"),
    ("↑ / ↓", "speed up / down"),
    ("← / →", "seek back / forward"),
    ("[ / ]", "previous / next block"),
    ("+ / -", "volume up / down"),
    ("k / j", "scroll up / down (free look)"),
    ("wheel", "scroll · PgUp/PgDn by page"),
    ("f", "follow the cursor again"),
    ("/text", "search forward"),
    ("?text", "search backward"),
    ("n / N", "repeat search fwd / back"),
    (":toc", "table of contents"),
    (":speed x", "set speed (e.g. :speed 1.5)"),
    (":vol x", "set volume (e.g. :vol 80)"),
    (":m:ss", "jump to time (e.g. :2:30)"),
    (":q", "quit  ·  :help  this screen"),
    ("q / esc", "quit"),
]


def _help_panel(console):
    text = Text()
    for key, desc in _HELP:
        text.append(f"  {key:<10}", style="bold cyan")
        text.append(f"{desc}\n", style="white")
    return Panel(
        Align(text, align="left", vertical="middle"),
        title="[bold magenta]✦ keys ✦[/bold magenta]",
        subtitle="[dim]press any key to return[/dim]",
        border_style="bright_magenta", padding=(1, 3), height=console.size.height,
    )


def _render(words, lo, hi, idx, title, subtitle, console):
    text = Text(justify="left")
    if lo > 0:
        text.append("… ", style="dim")
    for i in range(lo, hi):
        w = words[i]
        if _li_line_start(words, i, lo):
            text.append(_BULLET, style="dim" if i < idx else "cyan")
        if i == idx:
            style = "bold black on bright_cyan"  # the word being spoken
        elif i < idx:
            style = "dim"  # already spoken
        else:
            style = _KIND_STYLE.get(w.kind, "white")
        text.append(w.text, style=style)
        text.append(w.ws)
    if hi < len(words):
        text.append(" …", style="dim")

    return Panel(
        Align(text, align="left", vertical="top"),
        title=title,
        subtitle=subtitle,
        border_style="bright_magenta",
        padding=(1, 3),
        height=console.size.height,
    )


def _title(section):
    title = "[bold magenta]✦ read-along ✦[/bold magenta]"
    if section:
        title += f"  [dim]·[/dim]  [bold]{section}[/bold]"
    return title


def _subtitle(paused, speed, volume, pos, total):
    status = "[yellow]⏸[/yellow]" if paused else "[green]▶[/green]"
    pct = int(100 * pos / total) if total else 0
    vol = f" [dim]vol {int(volume)}%[/dim]" if int(volume) != 100 else ""
    return (
        f"{status} [cyan]{speed:g}×[/cyan]{vol}  "
        f"[dim]{pct}%  {_fmt_time(pos)}/{_fmt_time(total)}[/dim]   "
        "[dim]space · ←→ 10s · [ ] block · ↑↓ speed · +/- vol · q[/dim]"
    )


def read_along(audio_path, words, console, start_at=0.0, speed=1.0, volume=100,
               seek_step=SEEK_STEP, scroll_pause=False):
    """Play with the karaoke view. Returns the position on exit, or None if it
    played to the end (so the caller can clear a saved resume point)."""
    import mpv

    player = mpv.MPV(video=False, terminal=False, osc=False, input_default_bindings=False)
    ended = threading.Event()

    @player.event_callback("end-file")
    def _on_end(event):  # runs on mpv's thread; Event is thread-safe
        ended.set()

    starts = [w.start for w in words]
    sections = _build_sections(words)
    block_starts = _block_starts(words)
    fallback_total = words[-1].end if words else 0.0
    speed, volume = float(speed), float(volume)
    paused = False
    page_lo, page_hi = 0, _page_end(words, 0, console)
    last_size = console.size

    mode = "normal"  # normal | command | toc | help
    cmd_prefix, cmd_buffer, status_msg = "", "", ""
    last_search = ("", 1)
    headings = _headings(words)
    toc_sel = 0

    following = True       # view follows the cursor (vs free-look scrolling)
    line_cache = None      # _line_starts for the current width (lazy, for free look)
    top_line = 0           # top display line in free look
    scroll_paused = False  # we paused the audio for scrolling

    pos = start_at
    finished = False
    old_attrs = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        sys.stdout.write("\x1b[?1000h\x1b[?1006h")  # enable mouse (wheel) reporting
        sys.stdout.flush()
        if start_at > 0:  # resume: mpv's 'start' option begins playback here on load
            player.start = str(start_at)
        player.play(str(audio_path))
        player.speed = speed
        player.volume = volume
        with Live(
            _render(words, page_lo, page_hi, -1, _title(""), _subtitle(False, speed, volume, 0, fallback_total), console),
            console=console,
            refresh_per_second=FPS,
            screen=True,
        ) as live:
            while not ended.is_set():
                data = _read_raw()
                if data is not None and mode == "command":
                    if data in (b"\r", b"\n"):  # run the command
                        cur = bisect.bisect_right(starts, player.time_pos or 0.0) - 1
                        act = _parse_command(cmd_prefix + cmd_buffer, words, cur)
                        mode, cmd_buffer, status_msg = "normal", "", ""
                        if act[0] == "quit":
                            pos = player.time_pos or pos
                            break
                        elif act[0] == "seek":
                            player.seek(max(0.0, act[1]), reference="absolute", precision="exact")
                            following = True
                        elif act[0] == "search":
                            _seek_to_word(player, words, act[1])
                            last_search = (act[2], act[3])
                            following = True
                        elif act[0] == "speed":
                            speed = min(SPEED_MAX, max(SPEED_MIN, act[1]))
                            player.speed = speed
                        elif act[0] == "volume":
                            volume = min(130.0, max(0.0, act[1]))
                            player.volume = volume
                        elif act[0] == "mode":
                            mode = act[1]
                            player.pause = True  # pause while the overlay is open
                            if mode == "toc":
                                toc_sel = _current_heading(headings, cur)
                        elif act[0] == "message":
                            status_msg = act[1]
                    elif data == b"\x1b":  # cancel
                        mode, cmd_buffer = "normal", ""
                    elif data in (b"\x7f", b"\x08"):  # backspace
                        cmd_buffer = cmd_buffer[:-1]
                        if not cmd_buffer:
                            mode = "normal"
                    elif not data.startswith(b"\x1b"):  # ignore stray escape sequences
                        try:
                            cmd_buffer += data.decode("utf-8")
                        except UnicodeDecodeError:
                            pass
                elif data is not None and mode == "toc":
                    ev = _classify_key(data)
                    if data in (b"\r", b"\n"):
                        if headings:
                            _seek_to_word(player, words, headings[toc_sel][1])
                            following = True
                        mode = "normal"
                        player.pause = paused  # restore play state after the overlay
                    elif data in (b"\x1b", b"q"):
                        mode = "normal"
                        player.pause = paused
                    elif ev == "faster":  # up
                        toc_sel = max(0, toc_sel - 1)
                    elif ev == "slower" and headings:  # down
                        toc_sel = min(len(headings) - 1, toc_sel + 1)
                elif data is not None and mode == "help":
                    mode = "normal"  # any key dismisses
                    player.pause = paused  # restore play state after the overlay
                elif data is not None:  # normal mode
                    if data in (b"/", b"?", b":"):
                        mode, cmd_prefix, cmd_buffer, status_msg = "command", data.decode(), "", ""
                    else:
                        ev = _mouse_scroll(data) or _classify_key(data)
                        if ev == "pause":
                            paused = not paused
                            player.pause = paused
                        elif ev == "quit":
                            pos = player.time_pos or pos
                            break
                        elif ev == "faster":
                            speed = min(SPEED_MAX, round(speed + SPEED_STEP, 2))
                            player.speed = speed
                        elif ev == "slower":
                            speed = max(SPEED_MIN, round(speed - SPEED_STEP, 2))
                            player.speed = speed
                        elif ev == "louder":
                            volume = min(130.0, volume + 10)
                            player.volume = volume
                        elif ev == "softer":
                            volume = max(0.0, volume - 10)
                            player.volume = volume
                        elif ev == "forward":
                            player.seek(seek_step, reference="relative")
                            following = True
                        elif ev == "back":
                            player.seek(-seek_step, reference="relative")
                            following = True
                        elif ev in ("prev_block", "next_block"):
                            cur = bisect.bisect_right(starts, player.time_pos or 0.0) - 1
                            target = _target_block(block_starts, cur, -1 if ev == "prev_block" else 1)
                            if target is not None:
                                _seek_to_word(player, words, target)
                                following = True
                        elif ev in ("search_next", "search_prev"):
                            q, d = last_search
                            if q:
                                cur = bisect.bisect_right(starts, player.time_pos or 0.0) - 1
                                m = _search(words, cur, q, d if ev == "search_next" else -d)
                                if m is not None:
                                    _seek_to_word(player, words, m)
                                    following = True
                                else:
                                    status_msg = f"not found: {q}"
                        elif ev in ("scroll_up", "scroll_down", "page_up", "page_down"):
                            lpp = max(1, console.size.height - 4)
                            if line_cache is None:
                                line_cache = _line_starts(words, console)
                            if following:  # detach into free look at the current view
                                following = False
                                top_line = bisect.bisect_right(line_cache, page_lo) - 1
                                if scroll_pause and not paused:
                                    player.pause = True
                                    scroll_paused = True
                            step = {"scroll_up": -1, "scroll_down": 1, "page_up": -lpp, "page_down": lpp}[ev]
                            top_line = max(0, min(len(line_cache) - 1, top_line + step))
                        elif ev == "follow":
                            following = True
                            if scroll_paused:
                                player.pause = paused
                                scroll_paused = False

                pos = player.time_pos or pos
                idx = bisect.bisect_right(starts, pos) - 1

                if following:
                    # Re-paginate from the start on resize or a backwards jump, so
                    # the cursor lands in its natural page (already-read words show
                    # above it); otherwise the page holds still and flips only once
                    # the cursor leaves the bottom.
                    if console.size != last_size or idx < page_lo:
                        last_size = console.size
                        line_cache = None  # width may have changed; rebuild lazily
                        page_lo, page_hi = 0, _page_end(words, 0, console)
                    while idx >= page_hi < len(words):
                        page_lo = page_hi
                        page_hi = _page_end(words, page_lo, console)
                else:  # free look — view driven by top_line into the line table
                    if console.size != last_size or line_cache is None:
                        last_size = console.size
                        line_cache = _line_starts(words, console)
                        top_line = min(top_line, len(line_cache) - 1)
                    page_lo = line_cache[top_line]
                    page_hi = _page_end(words, page_lo, console)

                if mode == "toc":
                    live.update(_toc_panel(headings, toc_sel, console))
                elif mode == "help":
                    live.update(_help_panel(console))
                else:
                    total = player.duration or fallback_total
                    if following:
                        title = _title(sections[max(idx, 0)] if words else "")
                    else:
                        title = ("[bold magenta]✦ read-along ✦[/bold magenta]  "
                                 "[yellow]⊝ free look[/yellow]  [dim]· f: follow[/dim]")
                    if mode == "command":
                        subtitle = _command_bar(cmd_prefix, cmd_buffer)
                    else:
                        subtitle = _subtitle(paused, speed, volume, pos, total)
                        if status_msg:
                            subtitle = f"[yellow]{status_msg}[/yellow]   " + subtitle
                    live.update(_render(words, page_lo, page_hi, idx, title, subtitle, console))
                time.sleep(1 / FPS)
            else:
                # loop ended because the while condition went false (playback
                # finished) — not a quit. Decide this *before* the finally, since
                # player.terminate() fires its own end-file event.
                finished = True
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?1006l\x1b[?1000l")  # disable mouse reporting
        sys.stdout.flush()
        player.terminate()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_attrs)

    return None if finished else pos  # None = played to the end
