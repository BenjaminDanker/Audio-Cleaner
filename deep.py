import sys
import os
import traceback
import time
import shutil
from pathlib import Path
import imageio_ffmpeg
import subprocess, tempfile

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFileDialog, QSlider, QCheckBox, QMessageBox, QProgressBar
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QUrl, Qt, QThread, pyqtSignal

# --- DeepFilterNet Imports ---
try:
    import soundfile as sf
    from df.enhance import enhance, init_df, load_audio, save_audio
    # Change moviepy import to use the editor module
    from moviepy.video.io.VideoFileClip import VideoFileClip
    DEEPFILTER_AVAILABLE = True
except ImportError as e:
    print(f"Error importing dependencies: {e}")
    print("Please ensure deepfilternet, soundfile, moviepy imageio_ffmpeg are installed: pip install deepfilternet soundfile moviepy imageio-ffmpeg")
    DEEPFILTER_AVAILABLE = False
    class QThread: pass
    pyqtSignal = lambda *args, **kwargs: None

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # _MEIPASS not set, running in development mode
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def remux(source_path: str) -> str:
    """
    Strip Sony 'rtmd' metadata, keep video+audio, rebuild a clean MP4.
    """
    out_path = Path(tempfile.mkdtemp()) / ("remux_" + Path(source_path).name)
    ffmpeg  = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-ignore_editlist", "1",          # silence key‑frame warnings
        "-i", source_path,

        # keep **video** and **any** audio, drop everything else
        "-map", "0:v", "-map", "0:a?",    # ‑map syntax per FFmpeg wiki
        "-c", "copy",
        "-movflags", "+faststart",        # rebuild moov/index for streaming
        str(out_path)
    ]
    subprocess.run(cmd, check=True)
    return str(out_path)


# --- Worker Thread for Denoising ---
class DenoiseWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str, str)  # Output path, Status message
    error = pyqtSignal(str)

    def __init__(self, input_video, output_video, atten_lim_db):
        super().__init__()
        self.input_video = remux(input_video)
        self.output_video = output_video
        self.atten_lim_db = atten_lim_db
        self._is_running = True
        # Keep track of clips to ensure they are closed - only need original video clip now
        self.video_clip = None

    def run(self):
        if not DEEPFILTER_AVAILABLE:
            self.error.emit("Core dependencies (deepfilternet, moviepy, imageio_ffmpeg) not found.")
            return

        temp_dir = None # Initialize to None
        try:
            temp_dir = tempfile.mkdtemp() # Create temp dir first
            original_audio_path = os.path.join(temp_dir, "temp_original_audio.wav")
            enhanced_audio_path = os.path.join(temp_dir, "temp_enhanced_audio.wav")

            self.progress.emit(5)
            # 1. Extract Audio
            print("Extracting audio...")
            self.video_clip = None # Initialize video_clip to None

            # --- Add more robust check for video loading ---
            try:
                self.video_clip = VideoFileClip(self.input_video)
                if self.video_clip is None: # Explicit check after creation attempt
                    raise ValueError("VideoFileClip returned None.")
            except Exception as clip_error:
                print(f"Error opening video clip '{self.input_video}': {clip_error}")
                traceback.print_exc()
                self.error.emit(f"Failed to open input video:\n{clip_error}")
                return # Exit run method
            # --- End check ---


            if self.video_clip.audio is None:
                self.error.emit("Input video has no audio track.")
                # No need to return here yet, cleanup happens in finally
            else:
                # --- Add check before write_audiofile and wrap it ---
                print(f"Attempting to write audio to: {original_audio_path}")
                print(f"Audio object type: {type(self.video_clip.audio)}")
                if not hasattr(self.video_clip.audio, 'write_audiofile'):
                     # This case is unlikely but good for sanity check
                     print("Internal Error: MoviePy audio object missing 'write_audiofile'.")
                     self.error.emit("Internal error during audio processing (MoviePy).")
                else:
                    try:
                        # The actual write operation
                        self.video_clip.audio.write_audiofile(original_audio_path, codec='pcm_s16le')
                        print(f"Original audio saved to: {original_audio_path}")
                        self.progress.emit(20)
                    except Exception as audio_write_error:
                        # Catch errors specifically from write_audiofile (like the NoneType error)
                        print(f"Error during audio extraction (write_audiofile): {audio_write_error}")
                        traceback.print_exc() # Print the full traceback to console
                        self.error.emit(f"Failed to extract audio:\n{audio_write_error}")
                        return # Exit run method
                # --- End check and wrap ---

            # Close clip immediately after use
            if self.video_clip:
                self.video_clip.close()
                self.video_clip = None

            # Check if audio extraction actually succeeded before proceeding
            if not os.path.exists(original_audio_path):
                 # If write_audiofile failed silently or the audio was None initially
                 print("Audio extraction did not produce an output file.")
                 if not self.error.signal: # Avoid emitting a second error if one was already sent
                     self.error.emit("Audio extraction failed (no output file).")
                 return # Exit run method


            if not self._is_running: return # Check if stopped

            # 2. Denoise Audio using DeepFilterNet
            print("Initializing DeepFilterNet...")
            # Use resource_path to find the models directory
            model_path = resource_path("models/DeepFilterNet3")
            print(f"Looking for model at: {model_path}")
            model, df_state, _ = init_df(model_path, post_filter=True)
            print("Loading audio for denoising...")
            audio, sr = load_audio(original_audio_path, sr=df_state.sr())
            print(f"Audio loaded (Sample Rate: {sr} Hz)")
            self.progress.emit(40)

            if not self._is_running: return

            print(f"Enhancing audio... (atten_lim_db: {self.atten_lim_db})")
            enhanced_audio = enhance(model, df_state, audio, atten_lim_db=self.atten_lim_db)
            print("Enhancement complete.")
            self.progress.emit(70)

            if not self._is_running: return

            # --- Add check before save_audio ---
            print("Saving enhanced audio...")
            if enhanced_audio is None:
                print("Error: Enhanced audio data is None before saving.")
                self.error.emit("Denoising produced no audio data.")
                return # Exit run method
            try:
                save_audio(enhanced_audio_path, enhanced_audio, sr=df_state.sr())
                print(f"Enhanced audio saved to: {enhanced_audio_path}")
                self.progress.emit(80)
            except Exception as save_error:
                 print(f"Error saving enhanced audio: {save_error}")
                 traceback.print_exc()
                 self.error.emit(f"Failed to save denoised audio:\n{save_error}")
                 return # Exit run method
            # --- End check ---


            if not self._is_running: return # Check if stopped

            # 3. Replace Audio in Video using direct FFmpeg call
            print("Replacing audio in video using FFmpeg...")
            try:
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                print(f"Using FFmpeg executable: {ffmpeg_exe}")

                cmd = [
                    ffmpeg_exe,
                    "-y",                                 # overwrite output
                    "-ignore_editlist", "1",              # Ignore potentially problematic edit lists
                    "-i", self.input_video,              # original video (input #0)
                    "-i", enhanced_audio_path,           # new audio (input #1)
                    "-map", "0:v:0",                     # map video from input 0, stream 0
                    "-map", "1:a:0",                     # map audio from input 1, stream 0
                    "-c:v", "copy",                      # copy video stream
                    "-c:a", "aac",                       # encode audio stream to AAC
                    "-b:a", "320k",                      # audio bitrate (optional)
                    "-shortest",
                    self.output_video                    # output file path
                ]

                print(f"Running FFmpeg command: {' '.join(cmd)}")
                # Use capture_output=True and text=True for better debugging if needed
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                print("FFmpeg stdout:", result.stdout)
                print("FFmpeg stderr:", result.stderr) # FFmpeg often prints info to stderr
                print("FFmpeg command completed successfully.")

            except FileNotFoundError:
                 print("Error: FFmpeg executable not found. Make sure imageio-ffmpeg is installed correctly.")
                 self.error.emit("FFmpeg executable not found.")
                 return # Stop processing
            except subprocess.CalledProcessError as e:
                print(f"Error during FFmpeg execution: {e}")
                print("FFmpeg stdout:", e.stdout)
                print("FFmpeg stderr:", e.stderr)
                self.error.emit(f"FFmpeg error:\n{e.stderr or e.stdout or 'Unknown FFmpeg error'}")
                return # Stop processing
            except Exception as e: # Catch other potential errors
                print(f"An unexpected error occurred during FFmpeg processing: {e}")
                traceback.print_exc()
                self.error.emit(f"Unexpected error during FFmpeg processing: {e}")
                return

            print("Video processing complete.")
            self.progress.emit(100)
            self.finished.emit(self.output_video, f"Successfully denoised and saved to:\n{self.output_video}")

        except Exception as e:
            # General error handling for the entire process
            print(f"Unhandled error during denoising worker run: {e}")
            traceback.print_exc()
            # Avoid emitting error if one was already sent by specific steps
            if not self.error.signal:
                 self.error.emit(f"An unexpected error occurred:\n{e}")
        finally:
            # Ensure original video clip is closed if it exists
            if self.video_clip:
                try:
                    self.video_clip.close()
                    print("Video clip closed in finally block.")
                except Exception as close_err:
                    print(f"Error closing video clip in finally block: {close_err}")
            # Cleanup temp files only if temp_dir was created
            if temp_dir:
                self._cleanup(temp_dir)

    def _cleanup(self, temp_dir):
        print(f"Cleaning up temporary directory: {temp_dir}")
        # Add a small delay in case file handles need a moment to release
        time.sleep(0.5)
        try:
            if os.path.exists(temp_dir): # Check if dir exists before trying to list/remove
                for filename in os.listdir(temp_dir):
                    file_path = os.path.join(temp_dir, filename)
                    try:
                        os.remove(file_path)
                        print(f"Removed temp file: {file_path}")
                    except OSError as e:
                        print(f"Error removing file {file_path}: {e}") # Log specific file error
                try:
                    os.rmdir(temp_dir)
                    print("Removed temp directory.")
                except OSError as e:
                     print(f"Error removing directory {temp_dir}: {e}") # Log specific dir error
            else:
                print("Temporary directory does not exist, skipping cleanup.")

        except Exception as e:
            print(f"Error during cleanup: {e}") # Catch any other cleanup errors


    def stop(self):
        self._is_running = False
        print("Stop requested.")

# --- Main Application Window ---
class VideoDenoiserApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Audio Denoiser")
        self.setGeometry(100, 100, 800, 600)

        self.input_video_path = ""
        self.output_video_path = ""
        self.denoise_worker = None

        # --- Layouts ---
        self.main_layout = QVBoxLayout(self)
        self.file_layout = QHBoxLayout()
        self.controls_layout = QHBoxLayout()
        self.denoise_layout = QHBoxLayout()

        # --- Widgets ---
        # Input File
        self.input_label = QLabel("Input Video:")
        self.input_lineedit = QLineEdit()
        self.input_lineedit.setReadOnly(True)
        self.input_button = QPushButton("Browse...")
        self.input_button.clicked.connect(self.browse_input)

        # Output File
        self.output_label = QLabel("Output Video:")
        self.output_lineedit = QLineEdit()
        self.output_lineedit.setReadOnly(True)
        self.output_button = QPushButton("Select Save Location...")
        self.output_button.clicked.connect(self.browse_output)

        # Video Player
        self.video_widget = QVideoWidget()
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput() # Required for audio playback
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.errorOccurred.connect(self.handle_media_error)


        # Playback Controls
        self.play_button = QPushButton("Play")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_play)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setEnabled(False)
        self.seek_slider.sliderMoved.connect(self.set_position)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)


        # Denoise Controls
        self.atten_checkbox = QCheckBox("Use Default Attenuation (Recommended)\nLower = Better Sound Quality Retention\nHigher = Removing More Background Noise")
        self.atten_checkbox.setChecked(True)
        self.atten_checkbox.stateChanged.connect(self.toggle_atten_slider)

        self.atten_slider = QSlider(Qt.Orientation.Horizontal)
        self.atten_slider.setRange(1,60) # 1 to 60 dB limit
        self.atten_slider.setValue(30)
        self.atten_slider.setEnabled(False) # Disabled by default
        self.atten_slider.valueChanged.connect(self.update_atten_label)

        self.atten_label = QLabel("Limit: 30 dB") # Shows current slider value
        self.atten_label.setMinimumWidth(80)
        self.atten_label.setVisible(False) # Hidden by default

        self.denoise_button = QPushButton("Denoise Video")
        self.denoise_button.setEnabled(False)
        self.denoise_button.clicked.connect(self.start_denoising)

        # Progress Bar and Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_label = QLabel("Select input and output files.")

        # --- Assemble Layout ---
        self.file_layout.addWidget(self.input_label)
        self.file_layout.addWidget(self.input_lineedit)
        self.file_layout.addWidget(self.input_button)
        self.file_layout.addWidget(self.output_label)
        self.file_layout.addWidget(self.output_lineedit)
        self.file_layout.addWidget(self.output_button)

        self.controls_layout.addWidget(self.play_button)
        self.controls_layout.addWidget(self.seek_slider)

        self.denoise_layout.addWidget(self.atten_checkbox)
        self.denoise_layout.addWidget(self.atten_slider)
        self.denoise_layout.addWidget(self.atten_label)
        self.denoise_layout.addStretch() # Push button to the right
        self.denoise_layout.addWidget(self.denoise_button)


        self.main_layout.addLayout(self.file_layout)
        self.main_layout.addWidget(self.video_widget, stretch=1) # Video takes up most space
        self.main_layout.addLayout(self.controls_layout)
        self.main_layout.addLayout(self.denoise_layout)
        self.main_layout.addWidget(self.progress_bar)
        self.main_layout.addWidget(self.status_label)

        self.update_denoise_button_state() # Initial check

    def browse_input(self):
        fname, _ = QFileDialog.getOpenFileName(self, 'Open Video File', '', 'Video Files (*.mp4 *.avi *.mov *.mkv)')
        if fname:
            self.input_video_path = fname
            self.input_lineedit.setText(os.path.basename(fname))
            self.media_player.stop() # Stop previous playback
            self.media_player.setSource(QUrl.fromLocalFile(fname))
            self.play_button.setEnabled(True)
            self.seek_slider.setEnabled(True)
            self.play_button.setText("Play") # Set initial text
            self.status_label.setText("Input video loaded. Ready to play or denoise.")
            self.update_denoise_button_state()

            # Play and immediately pause to show the first frame
            self.media_player.play()
            self.media_player.pause()
            # Ensure slider is at the beginning
            self.seek_slider.setValue(0)

    def browse_output(self):
        fname, _ = QFileDialog.getSaveFileName(self, 'Save Denoised Video As...', '', 'MP4 Video Files (*.mp4)')
        if fname:
            # Ensure it has .mp4 extension
            if not fname.lower().endswith('.mp4'):
                fname += '.mp4'
            self.output_video_path = fname
            self.output_lineedit.setText(os.path.basename(fname))
            self.status_label.setText("Output path selected.")
            self.update_denoise_button_state()

    def update_denoise_button_state(self):
        enabled = bool(self.input_video_path and self.output_video_path and DEEPFILTER_AVAILABLE)
        self.denoise_button.setEnabled(enabled)
        if not DEEPFILTER_AVAILABLE:
             self.status_label.setText("Error: Core dependencies missing. Cannot denoise.")


    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_button.setText("Play")
        else:
            # If paused at the beginning, ensure it plays from the start
            if self.media_player.position() == 0 and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                 self.media_player.setPosition(0) # Explicitly set position just in case
            self.media_player.play()
            self.play_button.setText("Pause")

    def set_position(self, position):
        self.media_player.setPosition(position)

    def position_changed(self, position):
        self.seek_slider.setValue(position)

    def duration_changed(self, duration):
        self.seek_slider.setRange(0, duration)

    def handle_media_error(self, error, error_string=""):
         # Handle cases where error might be an enum or an integer
        error_code = error if isinstance(error, int) else error.value
        print(f"Media Player Error ({error_code}): {self.media_player.errorString()}")
        QMessageBox.warning(self, "Media Player Error", f"Could not play the video:\n{self.media_player.errorString()}")
        self.play_button.setEnabled(False)
        self.seek_slider.setEnabled(False)


    def toggle_atten_slider(self, state):
        use_default = (state == Qt.CheckState.Checked.value) # Qt6 uses enum value
        self.atten_slider.setEnabled(not use_default)
        self.atten_label.setVisible(not use_default)

    def update_atten_label(self, value):
        self.atten_label.setText(f"Limit: {value} dB")

    def start_denoising(self):
        if not self.input_video_path or not self.output_video_path:
            QMessageBox.warning(self, "Missing Information", "Please select both input and output video files.")
            return

        if self.denoise_worker and self.denoise_worker.isRunning():
            QMessageBox.information(self, "Busy", "Denoising process is already running.")
            return

        atten_limit = None
        if not self.atten_checkbox.isChecked():
            atten_limit = self.atten_slider.value()

        self.denoise_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting denoising process...")

        # Create and start the worker thread
        self.denoise_worker = DenoiseWorker(self.input_video_path, self.output_video_path, atten_limit)
        self.denoise_worker.progress.connect(self.update_progress)
        self.denoise_worker.finished.connect(self.denoising_finished)
        self.denoise_worker.error.connect(self.denoising_error)
        self.denoise_worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        self.status_label.setText(f"Denoising in progress... {value}%")

    def denoising_finished(self, output_path, message):
        self.progress_bar.setVisible(False)
        self.status_label.setText(message)
        QMessageBox.information(self, "Success", message)
        self.denoise_button.setEnabled(True) # Re-enable after success
        # Optionally load the denoised video
        self.media_player.stop()
        self.media_player.setSource(QUrl.fromLocalFile(output_path))
        self.play_button.setText("Play Denoised")
        self.play_button.setEnabled(True)
        self.seek_slider.setEnabled(True)


    def denoising_error(self, error_message):
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Error: {error_message}")
        QMessageBox.critical(self, "Denoising Error", error_message)
        self.denoise_button.setEnabled(True) # Re-enable after error

    def closeEvent(self, event):
        # Ensure worker thread is stopped if GUI is closed
        if self.denoise_worker and self.denoise_worker.isRunning():
            self.denoise_worker.stop()
            self.denoise_worker.wait() # Wait for thread to finish cleanly
        self.media_player.stop() # Stop media playback
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VideoDenoiserApp()
    window.show()
    sys.exit(app.exec())