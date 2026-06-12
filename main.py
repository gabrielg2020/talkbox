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

SOURCE_DIR = Path(__file__).resolve().parent / "source"
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

    words_count = sum(len(b.text.split()) for b in blocks)
    minutes = max(1, round(words_count / 150))  # ~150 wpm is a typical speaking pace
    console.print(
        f"\n[green]✓[/green] Voicing [bold]{source_path}[/bold] with [cyan]{engine_label(settings)}[/cyan]"
        f"  [dim]≈ {words_count:,} words, ~{minutes} min[/dim]\n"
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
        "[dim]read along:[/dim] pick [bold]📖 Read along[/bold] from the menu"
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

    # straight into read-along for Kokoro output, skipping a menu round-trip
    if result.words and questionary.confirm(
        "Read along now?", default=True, style=SELECT_STYLE, qmark="📖"
    ).ask():
        start_read_along(result.audio_path, result.words, settings)
    else:
        pause()


def _fmt_time(seconds):
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def start_read_along(audio_path, words, settings):
    """Launch read-along, with optional resume, reporting a missing mpv cleanly."""
    resume_on = settings.get("resume", True)

    start_at = 0.0
    if resume_on:
        saved = engines.load_position(audio_path)
        if saved and saved > 3 and questionary.confirm(
            f"Resume from {_fmt_time(saved)}?", default=True, style=SELECT_STYLE, qmark="▶"
        ).ask():
            start_at = saved

    try:
        from player import read_along

        final = read_along(
            audio_path, words, console, start_at,
            speed=settings.get("speed", 1.0),
            volume=settings.get("volume", 100),
            seek_step=settings.get("seek_step", 10),
        )
    except (ImportError, OSError):
        console.print(
            "[bold red]✗[/bold red] Read-along needs [bold]mpv[/bold] installed "
            "(e.g. [yellow]sudo pacman -S mpv[/yellow])."
        )
        pause()
        return

    if resume_on:
        if final is None:  # played to the end
            engines.clear_position(audio_path)
        else:
            engines.save_position(audio_path, final)


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


def do_read_along(settings):
    audio_files = sorted(
        p for ext in ("*.wav", "*.mp3") for p in engines.RECORDINGS_DIR.glob(ext)
    )
    if not audio_files:
        console.print("[bold red]✗[/bold red] No recordings found. Generate one first.")
        pause()
        return

    width = max(len(f.name) for f in audio_files)  # pad names so the labels align
    choices = [
        questionary.Choice(f"{f.name:<{width}}    [{recording_label(f)}]", value=f.name)
        for f in audio_files
    ]
    choice = pick("Which recording shall we read along to?", choices, qmark="📖", use_search_filter=True)
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

    start_read_along(audio_path, words, settings)


def do_manage_recordings():
    while True:
        audio_files = sorted(
            p for ext in ("*.wav", "*.mp3") for p in engines.RECORDINGS_DIR.glob(ext)
        )
        if not audio_files:
            console.print("[dim]No recordings to manage.[/dim]")
            pause()
            return

        width = max(len(f.name) for f in audio_files)  # pad names so the labels align
        choices = [
            questionary.Choice(f"{f.name:<{width}}    [{recording_label(f)}]", value=f.name)
            for f in audio_files
        ]
        choice = pick("Delete which recording?", choices, qmark="🤹")
        if choice is BACK:
            return

        if questionary.confirm(
            f"Delete {choice}?", default=False, style=SELECT_STYLE, qmark="🗑️"
        ).ask():
            path = engines.RECORDINGS_DIR / choice
            path.unlink(missing_ok=True)
            engines.meta_path(path).unlink(missing_ok=True)  # remove its sidecar too
            engines.clear_position(path)  # and any saved resume point
            console.print(f"[green]✓[/green] Deleted [bold]{choice}[/bold].")


def do_cache_voices():
    try:
        engines.cache_all_voices(console)
    except ImportError:
        console.print("[bold red]✗[/bold red] The Kokoro engine isn't installed.")
    pause()


def _settings_models(settings):
    engine = pick(
        "Which voice engine?",
        [
            questionary.Choice("Kokoro — local, accurate read-along", value="kokoro"),
            questionary.Choice("gTTS — fast, networked, no read-along", value="gtts"),
        ],
        qmark="🎙️",
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


SPEED_CHOICES = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
VOLUME_CHOICES = [60, 70, 80, 90, 100, 110, 120, 130]
SEEK_CHOICES = [5, 10, 15, 30]


def _settings_playback(settings):
    while True:
        resume = settings.get("resume", True)
        choice = pick(
            "Playback settings",
            [
                questionary.Choice(f"Resume from last position:  {'on' if resume else 'off'}", value="resume"),
                questionary.Choice(f"Default speed:  {settings.get('speed', 1.0):g}×", value="speed"),
                questionary.Choice(f"Default volume:  {settings.get('volume', 100)}%", value="volume"),
                questionary.Choice(f"Seek step (←/→):  {settings.get('seek_step', 10)}s", value="seek_step"),
            ],
            qmark="📖",
        )
        if choice is BACK:
            return settings

        if choice == "resume":
            settings = {**settings, "resume": not resume}
        elif choice == "speed":
            v = select("Default speed", [questionary.Choice(f"{s:g}×", value=s) for s in SPEED_CHOICES],
                       default=settings.get("speed", 1.0))
            if v is not None:
                settings = {**settings, "speed": v}
        elif choice == "volume":
            v = select("Default volume", [questionary.Choice(f"{s}%", value=s) for s in VOLUME_CHOICES],
                       default=settings.get("volume", 100))
            if v is not None:
                settings = {**settings, "volume": v}
        elif choice == "seek_step":
            v = select("Seek step", [questionary.Choice(f"{s}s", value=s) for s in SEEK_CHOICES],
                       default=settings.get("seek_step", 10))
            if v is not None:
                settings = {**settings, "seek_step": v}

        save_settings(settings)


def _playback_summary(settings):
    resume = "on" if settings.get("resume", True) else "off"
    return (
        f"resume {resume} · speed {settings.get('speed', 1.0):g}× · "
        f"vol {settings.get('volume', 100)}% · seek {settings.get('seek_step', 10)}s"
    )


def do_settings(settings):
    section = pick(
        "What would you like to configure?",
        [
            questionary.Choice(f"🎙️  Models    ·  {engine_label(settings)}", value="models"),
            questionary.Choice(f"📖  Playback  ·  {_playback_summary(settings)}", value="playback"),
        ],
        qmark="⚙️",
    )
    if section == "models":
        return _settings_models(settings)
    if section == "playback":
        return _settings_playback(settings)
    return settings  # BACK


def main():
    settings = load_settings()

    while True:
        show_start_screen()  # clear + banner each time, so old output doesn't pile up
        action = select(
            "What shall we do?",
            choices=[
                questionary.Choice("🎤  Generate speech from a text file", value="generate"),
                questionary.Choice("📖  Read along with a recording", value="read"),
                questionary.Choice("🤹  Manage recordings", value="manage"),
                questionary.Choice("📥  Download all voices for offline use", value="cache"),
                questionary.Choice(f"⚙️  Settings  [{engine_label(settings)}]", value="settings"),
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
            do_read_along(settings)
        elif action == "manage":
            do_manage_recordings()
        elif action == "cache":
            do_cache_voices()
        elif action == "settings":
            settings = do_settings(settings)


if __name__ == "__main__":
    main()
