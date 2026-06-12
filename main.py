import getpass
import sys
import warnings
from pathlib import Path

# urllib3 warns about LibreSSL on import on some platforms; it's harmless noise here.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import questionary
from pyfiglet import Figlet
from questionary import Style
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import engines
import loaders
from settings import KOKORO_VOICES, load_settings, save_settings

SOURCE_DIR = Path("source")
console = Console()

# Warm→cool gradient applied line-by-line across the figlet art.
GRADIENT = ["#ff5f87", "#ff5faf", "#d75fff", "#af5fff", "#5f87ff", "#5fd7ff"]

SELECT_STYLE = Style(
    [
        ("qmark", "fg:#ff79c6 bold"),
        ("question", "bold"),
        ("pointer", "fg:#50fa7b bold"),
        ("highlighted", "fg:#50fa7b bold"),
        ("selected", "fg:#8be9fd"),
        ("answer", "fg:#8be9fd bold"),
    ]
)


def banner_text():
    art = Figlet(font="ansi_shadow").renderText("talkbox").rstrip("\n").split("\n")
    text = Text(justify="center")
    for i, line in enumerate(art):
        text.append(line + "\n", style=f"bold {GRADIENT[i % len(GRADIENT)]}")
    text.append("((  text  ──▶  speech  ))", style="dim italic")
    return text


def show_start_screen():
    console.clear()
    console.print()
    console.print(
        Panel(
            Align.center(banner_text()),
            title=f"[bold magenta]✦ {getpass.getuser()}'s talkbox ✦[/bold magenta]",
            subtitle="[dim]turn your words into sound[/dim]",
            border_style="bright_magenta",
            padding=(1, 4),
        )
    )
    console.print()


BACK = object()  # sentinel returned by pick() for the "← Back" entry or cancel


def select(message, choices, **kwargs):
    return questionary.select(
        message,
        choices=choices,
        pointer="▶",
        use_jk_keys=False,
        style=SELECT_STYLE,
        **kwargs,
    ).ask()


def pick(message, items, **kwargs):
    """A select with an explicit '← Back' entry; returns BACK on back or Ctrl-C."""
    choices = [*items, questionary.Choice("← Back", value=BACK)]
    answer = select(message, choices, **kwargs)
    return BACK if answer is None else answer


def pause():
    """Hold the action's output on screen until the user is ready to return."""
    console.input("\n[dim]↵  press enter to return to the menu[/dim] ")


def engine_label(settings):
    if settings["engine"] == "kokoro":
        return f"Kokoro ({settings['voice']})"
    return "gTTS (fast, no read-along)"


def do_generate(settings):
    files = sorted(p for g in loaders.SUPPORTED_GLOBS for p in SOURCE_DIR.glob(g))
    if not files:
        console.print(f"[bold red]✗[/bold red] No documents found in [yellow]{SOURCE_DIR}/[/yellow]")
        pause()
        return

    choice = pick("Which file shall I read aloud?", [f.name for f in files], qmark="🎙️", use_search_filter=True)
    if choice is BACK:
        return

    source_path = SOURCE_DIR / choice

    caveat = loaders.caveat_for(source_path)
    if caveat and not questionary.confirm(
        f"⚠  {caveat}  Continue anyway?", default=True, style=SELECT_STYLE, qmark="⚠"
    ).ask():
        return

    try:
        blocks = loaders.load_blocks(source_path)
    except Exception as err:
        console.print(f"[bold red]✗[/bold red] Couldn't read [bold]{source_path}[/bold]: {err}")
        pause()
        return

    if not blocks:
        console.print(f"[bold yellow]![/bold yellow] [bold]{source_path}[/bold] has no readable text.")
        pause()
        return

    console.print(
        f"\n[green]✓[/green] Voicing [bold]{source_path}[/bold] with [cyan]{engine_label(settings)}[/cyan]\n"
    )

    try:
        result = engines.synthesize(blocks, source_path.name, settings["engine"], settings["voice"], console)
    except ImportError:
        console.print(
            "[bold red]✗[/bold red] The Kokoro engine isn't installed. "
            "Switch to gTTS in Settings, or reinstall dependencies."
        )
        pause()
        return

    read_hint = (
        "[dim]read along:[/dim] pick [bold]▶ Read along[/bold] from the menu"
        if result.words is not None
        else "[dim]no timing data — use the Kokoro engine for read-along[/dim]"
    )
    console.print()
    console.print(
        Panel(
            f"[bold green]done![/bold green]  saved → [bold cyan]{result.audio_path}[/bold cyan]\n"
            f"[dim]play it:[/dim] [yellow]ffplay {result.audio_path}[/yellow]\n{read_hint}",
            border_style="green",
            padding=(0, 2),
        )
    )
    pause()


def recording_label(audio_path):
    """How a recording was made: engine + voice, its source file, and whether
    that source still exists in source/."""
    meta = engines.load_meta(audio_path) or {}
    engine = meta.get("engine") or ("kokoro" if meta.get("words") else "gtts")
    label = f"Kokoro · {meta['voice']}" if engine == "kokoro" else "gTTS · no read-along"
    source = meta.get("source")
    if not source:
        return label  # legacy recording with no source recorded
    flag = "✓" if (SOURCE_DIR / source).exists() else "✗ missing"
    return f"{label} · from {source} {flag}"


def do_read_along():
    audio_files = sorted(
        p for ext in ("*.wav", "*.mp3") for p in engines.RECORDINGS_DIR.glob(ext)
    )
    if not audio_files:
        console.print("[bold red]✗[/bold red] No recordings found. Generate one first.")
        pause()
        return

    choices = [
        questionary.Choice(f"{f.name}    [{recording_label(f)}]", value=f.name) for f in audio_files
    ]
    choice = pick("Which recording shall we read along to?", choices, qmark="▶", use_search_filter=True)
    if choice is BACK:
        return

    audio_path = engines.RECORDINGS_DIR / choice
    words = engines.load_timings(audio_path)
    if not words:
        console.print(
            f"[bold yellow]![/bold yellow] [bold]{audio_path}[/bold] has no timing data.\n"
            "[dim]Read-along needs a file generated with the Kokoro engine.[/dim]"
        )
        pause()
        return

    try:
        from player import read_along

        read_along(audio_path, words, console)
    except (ImportError, OSError):
        console.print(
            "[bold red]✗[/bold red] Read-along needs [bold]mpv[/bold] installed "
            "(e.g. [yellow]sudo pacman -S mpv[/yellow])."
        )
        pause()


def do_cache_voices():
    try:
        engines.cache_all_voices(console)
    except ImportError:
        console.print("[bold red]✗[/bold red] The Kokoro engine isn't installed.")
    pause()


def do_settings(settings):
    engine = pick(
        "Which voice engine?",
        [
            questionary.Choice("Kokoro — local, accurate read-along", value="kokoro"),
            questionary.Choice("gTTS — fast, networked, no read-along", value="gtts"),
        ],
        qmark="⚙",
    )
    if engine is BACK:
        return settings

    settings = {**settings, "engine": engine}
    if engine == "kokoro":
        voice = pick("Which Kokoro voice?", KOKORO_VOICES, qmark="🗣️", default=settings["voice"])
        if voice is not BACK:
            settings["voice"] = voice

    save_settings(settings)
    console.print(f"[green]✓[/green] Saved. Using [cyan]{engine_label(settings)}[/cyan].")
    pause()
    return settings


def main():
    settings = load_settings()

    while True:
        show_start_screen()  # clear + banner each time, so old output doesn't pile up
        action = select(
            "What shall we do?",
            choices=[
                questionary.Choice("🎤  Generate speech from a text file", value="generate"),
                questionary.Choice("▶   Read along with a recording", value="read"),
                questionary.Choice("⬇   Download all voices for offline use", value="cache"),
                questionary.Choice(f"⚙   Settings  [{engine_label(settings)}]", value="settings"),
                questionary.Choice("🚪  Quit", value="quit"),
            ],
            qmark="✦",
        )
        if action in (None, "quit"):
            console.print("[dim]…until next time.[/dim]")
            return
        if action == "generate":
            do_generate(settings)
        elif action == "read":
            do_read_along()
        elif action == "cache":
            do_cache_voices()
        elif action == "settings":
            settings = do_settings(settings)


if __name__ == "__main__":
    main()
