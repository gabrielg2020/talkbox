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
        b"+": "louder",
        b"=": "louder",  # the unshifted '+' key
        b"-": "softer",
        b"\x1b": "quit",
    }.get(data)


def _read_key():
    """Return a semantic key event, or None.

    Reads with raw os.read so select() (kernel buffer) and the read agree — a
    buffered sys.stdin.read would slurp an arrow's trailing bytes into userspace,
    hiding them from select() and making arrows look like a bare Esc. The whole
    escape burst is grabbed at once; if a lone Esc arrives split from its
    sequence, a brief second look picks up the rest.
    """
    fd = sys.stdin.fileno()
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    data = os.read(fd, 16)
    if data == b"\x1b" and select.select([sys.stdin], [], [], 0.02)[0]:
        data += os.read(fd, 8)
    return _classify_key(data)


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


def read_along(audio_path, words, console):
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
    speed, volume = 1.0, 100.0
    paused = False
    page_lo, page_hi = 0, _page_end(words, 0, console)
    last_size = console.size

    old_attrs = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        player.play(str(audio_path))
        player.speed = speed
        with Live(
            _render(words, page_lo, page_hi, -1, _title(""), _subtitle(False, speed, volume, 0, fallback_total), console),
            console=console,
            refresh_per_second=FPS,
            screen=True,
        ) as live:
            while not ended.is_set():
                key = _read_key()
                if key == "pause":
                    paused = not paused
                    player.pause = paused
                elif key == "quit":
                    break
                elif key == "faster":
                    speed = min(SPEED_MAX, round(speed + SPEED_STEP, 2))
                    player.speed = speed
                elif key == "slower":
                    speed = max(SPEED_MIN, round(speed - SPEED_STEP, 2))
                    player.speed = speed
                elif key == "louder":
                    volume = min(130, volume + 10)
                    player.volume = volume
                elif key == "softer":
                    volume = max(0, volume - 10)
                    player.volume = volume
                elif key == "forward":
                    player.seek(SEEK_STEP, reference="relative")
                elif key == "back":
                    player.seek(-SEEK_STEP, reference="relative")
                elif key in ("prev_block", "next_block"):
                    cur = bisect.bisect_right(starts, player.time_pos or 0.0) - 1
                    target = _target_block(block_starts, cur, -1 if key == "prev_block" else 1)
                    if target is not None:
                        player.seek(words[target].start, reference="absolute", precision="exact")

                pos = player.time_pos or 0.0
                idx = bisect.bisect_right(starts, pos) - 1

                # Re-paginate from the start on resize or a backwards jump, so the
                # cursor lands in its natural page (already-read words show above
                # it); otherwise the page holds still and flips only once the
                # cursor leaves the bottom.
                if console.size != last_size or idx < page_lo:
                    last_size = console.size
                    page_lo, page_hi = 0, _page_end(words, 0, console)
                while idx >= page_hi < len(words):
                    page_lo = page_hi
                    page_hi = _page_end(words, page_lo, console)

                total = player.duration or fallback_total
                title = _title(sections[max(idx, 0)] if words else "")
                subtitle = _subtitle(paused, speed, volume, pos, total)
                live.update(_render(words, page_lo, page_hi, idx, title, subtitle, console))
                time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        player.terminate()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_attrs)
