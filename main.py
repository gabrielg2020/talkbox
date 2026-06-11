import getpass
import sys
import warnings
from pathlib import Path

# urllib3 warns about macOS's LibreSSL on import; it's harmless noise here.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import questionary
from gtts import gTTS
from pyfiglet import Figlet
from questionary import Style
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

SOURCE_DIR = Path("source")
console = Console()

# Warm→cool gradient applied line-by-line across the figlet art.
GRADIENT = ["#ff5f87", "#ff5faf", "#d75fff", "#af5fff", "#5f87ff", "#5fd7ff"]


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


def pick_file(txt_files):
    return questionary.select(
        "Which file shall I read aloud?",
        choices=[f.name for f in txt_files],
        qmark="🎙️",
        pointer="▶",
        use_search_filter=True,
        use_jk_keys=False,
        style=SELECT_STYLE,
    ).ask()


def synthesize(source_path):
    stem = source_path.stem
    text = source_path.read_text().rstrip()
    tts = gTTS(text)

    # gTTS splits text into parts; total lets the bar show a real ETA.
    parts = list(tts._tokenize(tts.text))
    out_path = Path(f"{stem}.mp3")

    progress = Progress(
        SpinnerColumn(spinner_name="dots12", style="bright_magenta"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None, complete_style="green", finished_style="bright_green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    with progress:
        task = progress.add_task(f"voicing {stem}", total=len(parts))
        with open(out_path, "wb") as mp3:
            for data in tts.stream():
                mp3.write(data)
                progress.advance(task)

    return out_path


def main():
    show_start_screen()

    txt_files = sorted(SOURCE_DIR.glob("*.txt"))
    if not txt_files:
        console.print(f"[bold red]✗[/bold red] No .txt files found in [yellow]{SOURCE_DIR}/[/yellow]")
        sys.exit(1)

    choice = pick_file(txt_files)
    if choice is None:  # Ctrl-C / Esc
        console.print("[dim]…maybe next time.[/dim]")
        sys.exit(0)

    source_path = SOURCE_DIR / choice
    console.print(f"\n[green]✓[/green] Reading [bold]{source_path}[/bold]\n")

    out_path = synthesize(source_path)

    console.print()
    console.print(
        Panel(
            f"[bold green]done![/bold green]  saved → [bold cyan]{out_path}[/bold cyan]\n"
            f"[dim]play it:[/dim] [yellow]ffplay {out_path}[/yellow]",
            border_style="green",
            padding=(0, 2),
        )
    )


if __name__ == "__main__":
    main()
