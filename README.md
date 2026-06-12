# 🎙️ talkbox

<p align="center">
  <img src="assets/logo.svg" alt="talkbox — text to speech" width="640">
</p>

A fun little terminal tool that turns text files into speech — with a colourful
ASCII start screen, an arrow-key file picker, and an optional **read-along**
view that highlights each word as it's spoken (karaoke-style).

Two voice engines:

- **Kokoro** (default) — runs locally and offline, and emits native per-word
  timestamps, so it can drive the read-along view. Saves `.wav`.
- **gTTS** — fast, uses Google's servers, no timing data (no read-along).
  Saves `.mp3`.

## How it works

1. Drop a `.txt`, `.md`, or `.docx` file into the `source/` folder.
2. Run the tool. From the menu you can **generate** speech, **read along** with
   a recording, **download all voices** for offline use, or change **settings**
   (engine + voice).
3. Generating voices the text with your chosen engine and saves the audio to
   `recordings/` (`.wav` for Kokoro, `.mp3` for gTTS). Each recording remembers
   the source file it came from, shown in the read-along picker.
4. Read-along plays a Kokoro recording back and highlights each word as it's
   spoken, a page at a time — `space` to pause, `↑`/`↓` to change speed (live,
   pitch-corrected), `q` or `Esc` to quit. Headings, paragraphs, and lists from
   `.md`/`.docx` are rendered with styling and read with natural pauses.

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

Read-along playback uses [mpv](https://mpv.io/), so install it once at system
level (it provides `libmpv`, which `python-mpv` binds to):

```bash
sudo pacman -S mpv      # or your distro's equivalent / `brew install mpv`
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
ffplay recordings/prp.wav      # any FFmpeg/audio player works
```

> Read-along plays audio itself. The `ffplay` hint is just for playing the file
> outside the app; a player isn't required to *generate* audio.

## Project layout

```
talkbox/
├── main.py            # TUI: start screen, menu, generate / read-along / voices / settings
├── engines.py         # synthesis + per-word timing (gTTS + Kokoro)
├── loaders.py         # read .txt / .md / .docx into plain text
├── player.py          # read-along karaoke playback (mpv)
├── settings.py        # persisted engine + voice (config.json)
├── requirements.txt   # dependencies
├── assets/            # README logo
├── docs/agent/        # design rationale and context
├── source/            # put your .txt/.md/.docx input files here (contents gitignored)
│   └── .gitkeep       # placeholder so the folder ships empty
└── recordings/        # generated audio + metadata sidecars (contents gitignored)
    └── .gitkeep
```

> The repo ships with empty `source/` and `recordings/` folders (just a
> `.gitkeep` each). Your inputs, the generated recordings and their
> `*.talkbox.json` metadata, and `config.json` are all gitignored, so drop your
> own files into `source/` to get started.

## Dependencies

| Package          | Why                                            |
|------------------|------------------------------------------------|
| `kokoro`         | Local TTS with native per-word timestamps      |
| `gtts`           | Google Text-to-Speech (fast, no timing)        |
| `soundfile`      | Write Kokoro audio to `.wav`                    |
| `python-mpv`     | Read-along playback with live, pitch-corrected speed |
| `markdown` + `beautifulsoup4` | Extract clean text from `.md`     |
| `python-docx`    | Extract text from `.docx`                      |
| `questionary`    | Arrow-key menus and file picker                |
| `rich`           | Colours, panels, progress bar, karaoke view    |
| `pyfiglet`       | ASCII-art banner                               |

## Notes

- Best viewed in a truecolor terminal (most modern terminals qualify) so the
  gradient banner and spinner render in full colour.
- Generated recordings (audio + metadata) live in `recordings/` and are
  gitignored; your `source/` inputs are kept locally too (also gitignored).
