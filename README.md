# 🎙️ talkbox

A fun little terminal tool that turns text files into spoken MP3s using Google
Text-to-Speech — with a colorful ASCII start screen, an arrow-key file picker,
and an animated progress bar.

```
████████╗ █████╗ ██╗     ██╗  ██╗██████╗  ██████╗ ██╗  ██╗
╚══██╔══╝██╔══██╗██║     ██║ ██╔╝██╔══██╗██╔═══██╗╚██╗██╔╝
   ██║   ███████║██║     █████╔╝ ██████╔╝██║   ██║ ╚███╔╝
   ██║   ██╔══██║██║     ██╔═██╗ ██╔══██╗██║   ██║ ██╔██╗
   ██║   ██║  ██║███████╗██║  ██╗██████╔╝╚██████╔╝██╔╝ ██╗
   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═╝
            ((  text  ──▶  speech  ))
```

## How it works

1. Drop a `.txt` file into the `source/` folder.
2. Run the tool and pick the file from the menu (type to filter, ↑/↓ to move,
   Enter to select).
3. It streams the audio from Google TTS, showing a live progress bar.
4. The result is saved as `<filename>.mp3` in the project root.

## Setup

Requires Python 3.9+ and an internet connection (gTTS calls Google's servers).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

Then just pick a file from the picker. When it finishes you'll get the output
path and a ready-to-paste play command:

```bash
ffplay PRP.mp3      # or: afplay PRP.mp3   (built into macOS)
```

> Playing the MP3 needs a player like [`ffplay`](https://ffmpeg.org/) (part of
> FFmpeg) or macOS's built-in `afplay`. Neither is required to *generate* the
> audio.

## Project layout

```
talkbox/
├── main.py            # the app
├── requirements.txt   # dependencies
├── source/            # put your .txt input files here (.txt contents gitignored)
│   └── .gitkeep       # placeholder so the folder ships empty
└── *.mp3              # generated output (gitignored)
```

> The repo ships with an empty `source/` (just a `.gitkeep`). Your `.txt`
> inputs and the generated `*.mp3` files are gitignored, so drop your own text
> files into `source/` to get started.

## Dependencies

| Package      | Why                                        |
|--------------|--------------------------------------------|
| `gtts`       | Google Text-to-Speech synthesis            |
| `questionary`| Arrow-key file picker                      |
| `rich`       | Colors, panels, and the animated progress bar |
| `pyfiglet`   | ASCII-art banner                           |

## Notes

- Best viewed in a truecolor terminal (iTerm2, modern Terminal.app) so the
  gradient banner and spinner render in full color.
- Generated `*.mp3` files are gitignored; your `source/*.txt` inputs are not.
