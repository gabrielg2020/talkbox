# 🎙️ talkbox

A fun little terminal tool that turns text files into speech — with a colourful
ASCII start screen, an arrow-key file picker, and an optional **read-along**
view that highlights each word as it's spoken (karaoke-style).

Two voice engines:

- **Kokoro** (default) — runs locally and offline, and emits native per-word
  timestamps, so it can drive the read-along view. Saves `.wav`.
- **gTTS** — fast, uses Google's servers, no timing data (no read-along).
  Saves `.mp3`.

<p align="center">
  <img src="assets/logo.svg" alt="talkbox — text to speech" width="640">
</p>

## How it works

1. Drop a `.txt` file into the `source/` folder.
2. Run the tool. From the menu you can **generate** speech, **read along** with
   a recording, **download all voices** for offline use, or change **settings**
   (engine + voice).
3. Generating voices the text with your chosen engine and saves the audio to
   the project root (`.wav` for Kokoro, `.mp3` for gTTS).
4. Read-along plays a Kokoro recording back and highlights each word as it's
   spoken, a page at a time — `space` to pause, `q` or `Esc` to quit.

## Setup

talkbox pins **Python 3.12** (the Kokoro engine's spaCy dependency doesn't build
on newer Pythons yet), managed with [`uv`](https://docs.astral.sh/uv/). Install
uv once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then set up the project:

```bash
uv python install 3.12
uv venv --python 3.12
# CPU-only torch keeps the Kokoro install ~200MB rather than pulling CUDA wheels
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements.txt
```

Everything is free and runs locally — no API keys or accounts. Kokoro downloads
its model on first use and each voice the first time you pick it; after that it
works fully offline. To grab everything ahead of time (e.g. before a trip with
no connection), use **Download all voices for offline use** from the menu. gTTS
always needs an internet connection.

## Usage

```bash
.venv/bin/python main.py
```

Pick **Generate**, choose a file, and it voices it. With the Kokoro engine you
can then pick **Read along** to watch it read back. You'll also get a
ready-to-paste play command:

```bash
ffplay prp.wav      # any FFmpeg/audio player works
```

> Read-along plays audio itself. The `ffplay` hint is just for playing the file
> outside the app; a player isn't required to *generate* audio.

## Project layout

```
talkbox/
├── main.py            # TUI: start screen, menu, generate / read-along / voices / settings
├── engines.py         # synthesis + per-word timing (gTTS + Kokoro)
├── player.py          # read-along karaoke playback
├── settings.py        # persisted engine + voice (config.json)
├── requirements.txt   # dependencies
├── assets/            # README logo
├── docs/agent/        # design rationale and context
├── source/            # put your .txt input files here (contents gitignored)
│   └── .gitkeep       # placeholder so the folder ships empty
└── *.wav / *.mp3      # generated output (gitignored)
```

> The repo ships with an empty `source/` (just a `.gitkeep`). Your `.txt`
> inputs, generated audio, the `*.talkbox.json` timing caches, and `config.json`
> are all gitignored, so drop your own text files into `source/` to get started.

## Dependencies

| Package         | Why                                            |
|-----------------|------------------------------------------------|
| `kokoro`        | Local TTS with native per-word timestamps      |
| `gtts`          | Google Text-to-Speech (fast, no timing)        |
| `soundfile`     | Write Kokoro audio to `.wav`                    |
| `just-playback` | Audio playback with live position (read-along) |
| `readchar`      | Key handling during read-along                 |
| `questionary`   | Arrow-key menus and file picker                |
| `rich`          | Colours, panels, progress bar, karaoke view    |
| `pyfiglet`      | ASCII-art banner                               |

## Notes

- Best viewed in a truecolor terminal (most modern terminals qualify) so the
  gradient banner and spinner render in full colour.
- Generated audio (`*.wav`/`*.mp3`) and timing caches are gitignored; your
  `source/*.txt` inputs are not.
