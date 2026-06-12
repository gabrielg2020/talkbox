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
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


@dataclass
class Word:
    """A spoken word with its audio time span, in seconds."""

    text: str
    ws: str  # trailing whitespace (incl. newlines that encode block breaks)
    start: float
    end: float
    kind: str = "p"  # source block kind: h1/h2/h3/p/li (default keeps old caches valid)


# Silence inserted after each block, in seconds — gives structure an audible beat.
PAUSE_AFTER = {"h1": 0.5, "h2": 0.5, "h3": 0.45, "p": 0.35, "li": 0.2}


@dataclass
class SynthResult:
    audio_path: Path
    words: list[Word] | None  # None when the engine gives no timing (gTTS)


def meta_path(audio_path):
    # Keep the full filename (incl. extension) so prp.wav and prp.mp3 get
    # distinct sidecars — otherwise a gTTS .mp3 would inherit a same-stem
    # Kokoro .wav's metadata.
    return audio_path.with_name(audio_path.name + ".talkbox.json")


def save_meta(audio_path, engine, source, voice, words):
    """Record how a recording was made next to it: engine, source file, voice,
    and (Kokoro only) per-word timings."""
    payload = {
        "engine": engine,
        "source": source,
        "voice": voice,
        "words": [asdict(w) for w in words] if words else None,
    }
    meta_path(audio_path).write_text(json.dumps(payload) + "\n")


def load_meta(audio_path):
    """Return a recording's metadata dict, or None if there's no sidecar."""
    path = meta_path(audio_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_timings(audio_path):
    """Return cached words for an audio file, or None if it has no timing."""
    meta = load_meta(audio_path)
    if not meta or not meta.get("words"):
        return None
    return [Word(**w) for w in meta["words"]]


def pin_recording(audio_path):
    """Mark a recording so the CLI stops offering to re-render it after a voice
    change. Cleared naturally when the recording is regenerated (fresh metadata)."""
    meta = load_meta(audio_path)
    if meta is not None:
        meta["pinned"] = True
        meta_path(audio_path).write_text(json.dumps(meta) + "\n")


# Resume positions: one small shared file keyed by recording name, rather than
# rewriting a recording's (large) metadata sidecar on every read-along exit.
POSITIONS_PATH = RECORDINGS_DIR / ".positions.json"


def _load_positions():
    if not POSITIONS_PATH.exists():
        return {}
    try:
        return json.loads(POSITIONS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_positions(positions):
    POSITIONS_PATH.write_text(json.dumps(positions) + "\n")


def load_position(audio_path):
    return _load_positions().get(audio_path.name)


def save_position(audio_path, seconds):
    positions = _load_positions()
    positions[audio_path.name] = round(seconds, 1)
    _write_positions(positions)


def clear_position(audio_path):
    positions = _load_positions()
    if positions.pop(audio_path.name, None) is not None:
        _write_positions(positions)


def _progress_bar(console):
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


def _synth_gtts(blocks, source, console):
    from gtts import gTTS

    stem = Path(source).stem
    tts = gTTS("\n\n".join(b.text for b in blocks))  # gTTS has no structure
    # gTTS splits text into parts; total lets the bar show a real ETA.
    parts = list(tts._tokenize(tts.text))
    out_path = RECORDINGS_DIR / f"{stem}.mp3"

    with _progress_bar(console) as progress:
        task = progress.add_task(f"voicing {stem}", total=len(parts))
        with open(out_path, "wb") as mp3:
            for data in tts.stream():
                mp3.write(data)
                progress.advance(task)

    save_meta(out_path, "gtts", source, voice=None, words=None)
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


def _block_break(block, next_block):
    """The whitespace that ends a block: a single newline keeps consecutive list
    items stacked; a blank line separates everything else."""
    if block.kind == "li" and next_block is not None and next_block.kind == "li":
        return "\n"
    return "\n\n"


_PIPELINE_CACHE = {}


def _get_pipeline(lang_code):
    """A cached KPipeline per language — avoids reloading the model on every
    generation. Voices load per call, so caching by lang_code is safe."""
    if lang_code not in _PIPELINE_CACHE:
        from kokoro import KPipeline

        # repo_id is explicit to suppress Kokoro's "defaulting repo_id" warning.
        _PIPELINE_CACHE[lang_code] = KPipeline(lang_code=lang_code, repo_id=KOKORO_REPO)
    return _PIPELINE_CACHE[lang_code]


def _run_kokoro_blocks(blocks, voice, advance=None):
    """Synthesise each block on its own, so every word carries its block's kind.

    Doing it per block (rather than one flat string) gives an exact word→style
    link with no offset-guessing, and lets us drop a silence between blocks so the
    structure is audible. Token timestamps are chunk-relative, so each is offset
    by the audio elapsed so far — silences included. ``advance(n)`` (optional) is
    called with each block's word count to drive a progress bar.
    """
    import numpy as np

    # Voice prefix selects accent: 'a' = American, 'b' = British.
    pipeline = _get_pipeline(voice[0])

    audio_parts, words, elapsed = [], [], 0.0
    for i, block in enumerate(blocks):
        block_words = []
        for result in pipeline(block.text, voice=voice):
            audio_parts.append(result.audio.numpy())
            if result.tokens:
                block_words.extend(_merge_tokens(result.tokens, elapsed))
            elapsed += len(audio_parts[-1]) / KOKORO_SAMPLE_RATE

        if block_words:
            for w in block_words:
                w.kind = block.kind
            next_block = blocks[i + 1] if i + 1 < len(blocks) else None
            block_words[-1].ws = block_words[-1].ws.rstrip() + _block_break(block, next_block)
            words.extend(block_words)

        if i + 1 < len(blocks):  # silence between blocks, not after the last
            pause = PAUSE_AFTER.get(block.kind, 0.3)
            audio_parts.append(np.zeros(int(pause * KOKORO_SAMPLE_RATE), dtype=np.float32))
            elapsed += pause

        if advance:
            advance(len(block.text.split()))

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


def _synth_kokoro(blocks, source, voice, console):
    _quiet_kokoro_noise()

    import huggingface_hub.constants as hf
    import numpy as np
    import soundfile as sf
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled

    stem = Path(source).stem
    # Go offline only when this voice is already cached: skips the hub round-trip
    # (faster, no "unauthenticated requests" notice, works on a train) while still
    # letting a never-used voice download. is_offline_mode() reads this live, so
    # toggling per-synth handles switching voices within a session.
    hf.HF_HUB_OFFLINE = _voice_cached(voice)
    total_words = sum(len(b.text.split()) for b in blocks) or 1
    with _progress_bar(console) as progress:
        task = progress.add_task(f"voicing {stem}", total=total_words)
        advance = lambda n: progress.advance(task, n)
        try:
            audio_parts, words = _run_kokoro_blocks(blocks, voice, advance)
        except (LocalEntryNotFoundError, OfflineModeIsEnabled):
            hf.HF_HUB_OFFLINE = False  # asset wasn't cached after all — fetch it
            progress.reset(task)
            audio_parts, words = _run_kokoro_blocks(blocks, voice, advance)

    out_path = RECORDINGS_DIR / f"{stem}.wav"
    sf.write(out_path, np.concatenate(audio_parts), KOKORO_SAMPLE_RATE)
    save_meta(out_path, "kokoro", source, voice, words)
    return SynthResult(out_path, words=words)


def synthesize(blocks, source, engine, voice, console):
    """Synthesise ``blocks`` to a recording, remembering the ``source`` filename."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    if engine == "kokoro":
        return _synth_kokoro(blocks, source, voice, console)
    return _synth_gtts(blocks, source, console)
