"""Read-along playback: highlight each word as Kokoro speaks it.

Single-threaded — keys are polled with select() under cbreak mode rather than a
background reader thread, so nothing lingers to swallow keystrokes once playback
ends and the menu comes back.
"""

import bisect
import select
import sys
import termios
import time
import tty

from rich.align import Align
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

FPS = 30


def _page_end(words, start, console):
    """Index one past the last word that fits on a page starting at ``start``.

    Measures real wrapped-line widths so a page holds exactly what the box can
    show. The text stays put within a page; we only ever advance by whole pages,
    so words never drift under the reader's eyes mid-read.
    """
    interior_w = max(10, console.size.width - 8)    # borders + horizontal padding
    interior_h = max(2, console.size.height - 4)    # borders + vertical padding
    i, lines, col = start, 1, 0
    while i < len(words):
        wlen = len(words[i].text) + len(words[i].ws)
        if col + wlen > interior_w:
            lines += 1
            if lines > interior_h:
                break
            col = 0
        col += wlen
        i += 1
    return i


def _render(words, lo, hi, idx, paused, console):
    text = Text(justify="left")
    if lo > 0:
        text.append("… ", style="dim")
    for i in range(lo, hi):
        if i == idx:
            style = "bold black on bright_cyan"
        elif i < idx:
            style = "dim"
        else:
            style = "white"
        text.append(words[i].text, style=style)
        text.append(words[i].ws)
    if hi < len(words):
        text.append(" …", style="dim")

    status = "[yellow]⏸ paused[/yellow]" if paused else "[green]▶ playing[/green]"
    return Panel(
        Align.center(text, vertical="top"),
        title="[bold magenta]✦ read-along ✦[/bold magenta]",
        subtitle=f"{status}   [dim]·  space: pause  ·  q: quit[/dim]",
        border_style="bright_magenta",
        padding=(1, 3),
        height=console.size.height,
    )


def read_along(audio_path, words, console):
    from just_playback import Playback

    playback = Playback()
    playback.load_file(str(audio_path))
    starts = [w.start for w in words]

    old_attrs = termios.tcgetattr(sys.stdin)
    paused = False
    page_lo = 0
    page_hi = _page_end(words, page_lo, console)
    last_size = console.size
    try:
        tty.setcbreak(sys.stdin.fileno())
        playback.play()
        with Live(
            _render(words, page_lo, page_hi, -1, paused, console),
            console=console,
            refresh_per_second=FPS,
            screen=True,
        ) as live:
            while playback.active:
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch == " ":
                        paused = not paused
                        playback.pause() if paused else playback.resume()
                    elif ch in ("q", "\x1b"):
                        break
                idx = bisect.bisect_right(starts, playback.curr_pos) - 1

                # On resize (e.g. adjusting a tmux pane), the page was wrapped for
                # the old size. Re-paginate from the start so the cursor lands in
                # its natural page — the already-read words in that page show above
                # it (and expanding reveals more), rather than snapping the cursor
                # to the top with nothing behind it.
                if console.size != last_size:
                    last_size = console.size
                    page_lo, page_hi = 0, _page_end(words, 0, console)

                # Advance to the page holding the cursor: a no-op mid-page, one flip
                # at a page boundary, several in a row right after a resize.
                while idx >= page_hi < len(words):
                    page_lo = page_hi
                    page_hi = _page_end(words, page_lo, console)

                live.update(_render(words, page_lo, page_hi, idx, paused, console))
                time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        playback.stop()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_attrs)
