# Audio Cleaner

A cross‑platform desktop application that **removes background noise from any video’s audio track** using the
[DeepFilterNet 3] neural noise‑suppression model and an intuitive **PyQt 6** interface.  
It extracts the soundtrack, denoises it with DeepFilterNet, then remuxes the cleaned audio back into the
original video with FFmpeg – all in one click.

---

## Key Features

* **Point‑and‑click GUI** built with PyQt 6 – no command line needed.  
* **Preview player** – play the source or cleaned video inside the app.  
* Adjustable **attenuation limit slider** (1‑60 dB) or a safe recommended default.  
* **Progress bar & status messages** courtesy of a background QThread worker.  
* Automatic **remux** step strips problematic metadata (e.g. Sony *rtmd*) before processing.  
* Works with **MP4, MOV, AVI, MKV** and any format FFmpeg can decode.  
* Cross‑platform: Windows, macOS, Linux (tested on Python 3.9+).  
* Clean shutdown & temp‑file cleanup even on errors.  

---

## Quick Start

```bash
# 1 – Clone this repository
git clone https://github.com/your‑username/audio-cleaner.git
cd audio-cleaner

# 2 – Create & activate a virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# 3 – Install dependencies
pip install -r requirements.txt

# 4 – Download the DeepFilterNet 3 model (≈ 30 MB)
python -m df --download-models

# 5 – Run the app
python deep.py
```

The first launch will prompt you to select an **input video** and a **save location**.  
Click **“Denoise Video”** and let the progress bar reach 100 %. Voilà – background noise gone!

---

## Requirements

| Package | Tested Version | Notes |
|---------|---------------|-------|
| **Python** | 3.9 – 3.12 | |
| [deepfilternet] | ≥ 0.4 |
| **PyQt6** | ≥ 6.5 | GUI, multimedia widgets |
| **moviepy** | ≥ 1.0 | Video I/O |
| **soundfile** | ≥ 0.12 | WAV read/write |
| **imageio‑ffmpeg** | ≥ 0.4 | FFmpeg binary download helper |
| **Torch** |
| **TorchAudio** |

A pre‑filled **`requirements.txt`** is provided for your convenience.

---

## How It Works

```text
           ┌────────────┐ 1. Extract audio with MoviePy
Input MP4 ─► Remux step ├──────────┐
           └────────────┘          │
                                   ▼
                            2. Denoise WAV
                               with DF‑Net
                                   ▼
           ┌────────────┐ 3. Combine audio & video with FFmpeg
Clean MP4 ◄─┤  FFmpeg   │
           └────────────┘
```

The heavy lifting is done in `DenoiseWorker` (see **deep.py**) running in its own
QThread so the UI stays responsive.

---

## Packaging a Standalone Executable *(optional)*

Install PyInstaller, then:

```bash
pyinstaller --noconfirm --windowed --onefile \
  --add-data "models/DeepFilterNet3:models/DeepFilterNet3" \
  --add-binary "$(python - <<'PY'
import imageio_ffmpeg, sys, os; print(imageio_ffmpeg.get_ffmpeg_exe()+os.pathsep+'ffmpeg')
PY)":. \
  deep.py
```

The resulting **`dist/deep`** (or **`deep.exe`** on Windows) can be shipped
without requiring Python on the end‑user’s machine.

---

## Troubleshooting

| Symptom | Possible Cause / Fix |
|---------|----------------------|
| **“Core dependencies missing”** in status bar | `pip install -r requirements.txt` |
| No audio in output | Set console=true in VideoDenoiser.spec when creating an executable |
| **FFmpeg executable not found** | `pip install imageio‑ffmpeg` or add FFmpeg to PATH. |
| Crash when loading video | Some raw camera files have invalid edit lists – the built‑in *remux* step usually fixes this. |

---

## License

Distributed under the **MIT License** – see [`LICENSE`](LICENSE) for details.

---

## Acknowledgements

* [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) by **A. Rosenkranz et al.**
* [MoviePy](https://zulko.github.io/moviepy/) – video editing in Python  
* **FFmpeg** – the Swiss‑Army knife of video processing
