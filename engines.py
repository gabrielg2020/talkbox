"""Speech synthesis engines and per-word timing.

Two engines:
  - gTTS    — fast, networked, no timing data (read-along unavailable).
  - Kokoro  — local, offline, emits native per-word timestamps for read-along.

Kokoro timings are cached next to the audio as ``<stem>.talkbox.json`` so a
file only has to be aligned once.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO = "hexgrad/Kokoro-82M"


@dataclass
class Word:
    """A spoken word with its audio time span, in seconds."""

    text: str
    ws: str  # trailing whitespace, so the original text reconstructs exactly
    start: float
    end: float


@dataclass
class SynthResult:
    audio_path: Path
    words: list[Word] | None  # None when the engine gives no timing (gTTS)


def timing_path(audio_path):
    # Keep the full filename (incl. extension) so prp.wav and prp.mp3 get
    # distinct sidecars — otherwise a gTTS .mp3 would inherit a same-stem
    # Kokoro .wav's timing.
    return audio_path.with_name(audio_path.name + ".talkbox.json")


def save_timings(audio_path, voice, words):
    payload = {"voice": voice, "words": [asdict(w) for w in words]}
    timing_path(audio_path).write_text(json.dumps(payload) + "\n")


def load_timings(audio_path):
    """Return cached words for an audio file, or None if there's no timing sidecar."""
    path = timing_path(audio_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return [Word(**w) for w in payload["words"]]


def describe(audio_path):
    """Short label of how a recording was made: Kokoro + voice, or gTTS.

    A timing sidecar only exists for Kokoro output, so its absence means gTTS
    (which carries no timing and can't be read along).
    """
    path = timing_path(audio_path)
    if not path.exists():
        return "gTTS · no read-along"
    try:
        voice = json.loads(path.read_text()).get("voice")
    except (json.JSONDecodeError, OSError):
        return "gTTS · no read-along"
    return f"Kokoro · {voice}" if voice else "Kokoro"


def _gtts_progress(console):
    return Progress(
        SpinnerColumn(spinner_name="dots12", style="bright_magenta"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None, complete_style="green", finished_style="bright_green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def _synth_gtts(text, stem, console):
    from gtts import gTTS

    tts = gTTS(text)
    # gTTS splits text into parts; total lets the bar show a real ETA.
    parts = list(tts._tokenize(tts.text))
    out_path = Path(f"{stem}.mp3")

    with _gtts_progress(console) as progress:
        task = progress.add_task(f"voicing {stem}", total=len(parts))
        with open(out_path, "wb") as mp3:
            for data in tts.stream():
                mp3.write(data)
                progress.advance(task)

    return SynthResult(out_path, words=None)


def _merge_tokens(tokens, elapsed):
    """Turn Kokoro tokens (offset by ``elapsed`` seconds) into Words.

    Pure-punctuation tokens are folded into the preceding word so a full stop
    highlights together with its word rather than on its own.
    """
    words = []
    for tok in tokens:
        if tok.start_ts is None:  # rare: token without timing
            continue
        start, end = elapsed + tok.start_ts, elapsed + tok.end_ts
        is_punct = not any(c.isalnum() for c in tok.text)
        if is_punct and words:
            words[-1].text += tok.text
            words[-1].end = end
            words[-1].ws = tok.whitespace
        else:
            words.append(Word(tok.text, tok.whitespace, start, end))
    return words


def _quiet_kokoro_noise():
    """Silence harmless load-time chatter from torch/HF so it doesn't clutter the TUI."""
    import logging
    import warnings

    warnings.filterwarnings("ignore", category=UserWarning, module=r"torch(\..*)?")
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"torch(\..*)?")
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


def _voice_cached(voice):
    """Whether this voice file is already in the HF cache (so we can skip the network)."""
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(KOKORO_REPO, f"voices/{voice}.pt"), str)


def _run_kokoro(text, voice):
    from kokoro import KPipeline

    # Voice prefix selects accent: 'a' = American, 'b' = British.
    # repo_id is passed explicitly to suppress Kokoro's "defaulting repo_id" warning.
    pipeline = KPipeline(lang_code=voice[0], repo_id=KOKORO_REPO)

    audio_parts, words, elapsed = [], [], 0.0
    for result in pipeline(text, voice=voice):
        audio_parts.append(result.audio.numpy())
        if result.tokens:
            words.extend(_merge_tokens(result.tokens, elapsed))
        elapsed += len(audio_parts[-1]) / KOKORO_SAMPLE_RATE
    return audio_parts, words


def cache_all_voices(console):
    """Download the full Kokoro model and every voice for offline use.

    One snapshot pulls the model plus all voice files, so read-along works with no
    connection afterwards — handy before a trip. Returns True on success.
    """
    _quiet_kokoro_noise()
    import huggingface_hub.constants as hf
    from huggingface_hub import snapshot_download

    hf.HF_HUB_OFFLINE = False  # we explicitly want to fetch everything
    console.print("[cyan]Downloading the Kokoro model and all voices…[/cyan]")
    try:
        snapshot_download(KOKORO_REPO)
    except Exception as err:  # offline, network, or hub error
        console.print(f"[bold red]✗[/bold red] Couldn't download: {err}")
        return False
    console.print("[green]✓[/green] All voices cached — read-along now works fully offline.")
    return True


def _synth_kokoro(text, stem, voice, console):
    _quiet_kokoro_noise()

    import huggingface_hub.constants as hf
    import numpy as np
    import soundfile as sf
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled

    # Go offline only when this voice is already cached: skips the hub round-trip
    # (faster, no "unauthenticated requests" notice, works on a train) while still
    # letting a never-used voice download. is_offline_mode() reads this live, so
    # toggling per-synth handles switching voices within a session.
    hf.HF_HUB_OFFLINE = _voice_cached(voice)
    with console.status(f"[bold cyan]voicing {stem} with Kokoro…", spinner="dots12"):
        try:
            audio_parts, words = _run_kokoro(text, voice)
        except (LocalEntryNotFoundError, OfflineModeIsEnabled):
            hf.HF_HUB_OFFLINE = False  # asset wasn't cached after all — fetch it
            audio_parts, words = _run_kokoro(text, voice)

    out_path = Path(f"{stem}.wav")
    sf.write(out_path, np.concatenate(audio_parts), KOKORO_SAMPLE_RATE)
    save_timings(out_path, voice, words)
    return SynthResult(out_path, words=words)


def synthesize(text, stem, engine, voice, console):
    if engine == "kokoro":
        return _synth_kokoro(text, stem, voice, console)
    return _synth_gtts(text, stem, console)
