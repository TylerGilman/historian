import sys
import os
import random
import tempfile
import platform
import shutil
import time
import uuid
import json
import math
import subprocess
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QLabel,
    QFileDialog,
    QMessageBox,
    QAbstractItemView,
    QProgressDialog,
    QDialog,
    QSpinBox,
    QCheckBox,
    QSplitter,
    QToolButton,
    QSizePolicy,
    QStyle,
    QFrame,
    QTabWidget,
    QInputDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDoubleSpinBox,
    QComboBox,
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QMediaPlaylist
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import (
    Qt,
    QUrl,
    QThread,
    pyqtSignal,
    QSize,
    QObject,
    QMetaObject,
    Q_ARG,
    QRect,
    QTimer,
)
from PyQt5.QtGui import QIcon, QFont, QPalette, QColor, QPainter, QPen

# Create dedicated temp directory
TEMP_DIR = os.path.join(tempfile.gettempdir(), "video_editor_temp")
os.makedirs(TEMP_DIR, exist_ok=True)

# Create preview directory
PREVIEW_DIR = os.path.join(TEMP_DIR, "previews")
os.makedirs(PREVIEW_DIR, exist_ok=True)


def clean_directory(directory):
    """Clean a directory with error handling"""
    try:
        for item in os.listdir(directory):
            path = os.path.join(directory, item)
            try:
                if os.path.isfile(path) or os.path.islink(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception as e:
                print(f"Warning: Failed to delete {path}: {e}")
    except Exception as e:
        print(f"Warning: Error cleaning directory {directory}: {e}")


def check_hw_encoders():
    """Check for available hardware encoders"""
    encoders = []

    # Check for NVIDIA encoder
    try:
        nvidia = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if "h264_nvenc" in nvidia.stdout:
            encoders.append("h264_nvenc")
    except:
        pass

    # Check for VA-API (Intel/AMD on Linux)
    try:
        if os.path.exists("/dev/dri"):
            encoders.append("h264_vaapi")
    except:
        pass

    # Check for QuickSync (Intel)
    try:
        qsv = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if "h264_qsv" in qsv.stdout:
            encoders.append("h264_qsv")
    except:
        pass

    # If no HW encoders found, use libx264 (CPU)
    if not encoders:
        encoders.append("libx264")

    return encoders


def cleanup_temp_dirs():
    """Clean up all temp directories"""
    try:
        clean_directory(PREVIEW_DIR)
        clean_directory(TEMP_DIR)
        print("Temporary directories cleaned")
    except Exception as e:
        print(f"Warning: Error during cleanup: {e}")


class MediaItem:
    """Base class for video and image items"""

    def __init__(self, file_path, is_image=False):
        self.file_path = file_path
        self.is_image = is_image
        self.display_duration = 5.0  # Default duration for images (seconds)
        self.start_time = 0
        self.end_time = None
        self.duration = None
        self.rotation = 0
        self.manual_rotation = 0  # Default manual rotation (89 degrees)
        self.preview_file = None  # Path to cached preview
        self.preview_status = "none"  # none, generating, ready, error
        self.item_id = str(uuid.uuid4())[:8]  # Unique ID for this item
        self.effects = []  # List of VideoEffect objects applied to this item
        self.playback_speed = 1.0  # Default playback speed
        self.has_pending_changes = False  # Indicator for unsaved changes

    def get_preview_filename(self):
        """Generate a unique filename for preview"""
        name = os.path.basename(self.file_path)
        base, _ = os.path.splitext(name)
        base = base.replace(" ", "_")

        # Include effects in the filename to ensure unique cache
        effects_hash = ""
        if self.effects:
            effects_str = "_".join(e.effect_type for e in self.effects)
            effects_hash = f"_fx{hash(effects_str) % 10000}"

        # Include speed in filename for cache uniqueness
        speed_str = f"_sp{int(self.playback_speed*100)}"

        if self.is_image:
            return os.path.join(
                PREVIEW_DIR,
                f"{base}_{self.item_id}_d{self.display_duration}_r{self.manual_rotation}{effects_hash}.mp4",
            )
        else:
            return os.path.join(
                PREVIEW_DIR,
                f"{base}_{self.item_id}_s{self.start_time}_e{self.end_time or self.duration}_r{self.manual_rotation}{effects_hash}{speed_str}.mp4",
            )

    def invalidate_preview(self):
        """Mark the preview as invalid"""
        if self.preview_file and os.path.exists(self.preview_file):
            try:
                os.unlink(self.preview_file)
            except Exception as e:
                print(f"Warning: Error deleting preview file: {e}")
        self.preview_file = None
        self.preview_status = "none"
        self.has_pending_changes = True

    def get_effects_filter_string(self):
        """Get the combined filter string for all effects"""
        filters = []
        for effect in self.effects:
            effect_filter = effect.get_ffmpeg_filter()
            if effect_filter:
                filters.append(effect_filter)

        # Add speed adjustment if not 1.0
        if self.playback_speed != 1.0 and not any(
            e.effect_type == "speed" for e in self.effects
        ):
            # Create a speed effect
            speed_effect = VideoEffect("speed", {"factor": self.playback_speed})
            filters.append(speed_effect.get_ffmpeg_filter())

        if filters:
            return ",".join(filters)
        return ""


class VideoClip(MediaItem):
    def __init__(self, file_path):
        super().__init__(file_path, is_image=False)
        self.codec = None
        self.bit_depth = None
        self.width = 0
        self.height = 0
        self.pixel_format = "yuv420p"

        try:
            # Use subprocess for stability with ffprobe
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed: {result.stderr}")

            probe = json.loads(result.stdout)

            self.duration = float(probe["format"]["duration"])
            self.end_time = self.duration

            for stream in probe["streams"]:
                if stream["codec_type"] == "video":
                    self.codec = stream.get("codec_name", "unknown")
                    self.bit_depth = int(stream.get("bits_per_raw_sample", 8))
                    self.width = int(stream.get("width", 0))
                    self.height = int(stream.get("height", 0))
                    self.pixel_format = stream.get("pix_fmt", "yuv420p")

                    # Handle rotation metadata
                    if "tags" in stream and "rotate" in stream["tags"]:
                        self.rotation = int(stream["tags"]["rotate"])

                    # Check for side data rotation
                    if "side_data_list" in stream:
                        for side_data in stream["side_data_list"]:
                            if side_data.get("side_data_type") == "Display Matrix":
                                # Parse rotation from display matrix
                                rotation_data = side_data.get("rotation", "")
                                if rotation_data:
                                    try:
                                        # Handle both string and numeric rotation values
                                        if isinstance(rotation_data, (int, float)):
                                            rotation_val = float(rotation_data)
                                        else:
                                            # Assume it's a string like "-90.00 degrees"
                                            rotation_val = float(
                                                rotation_data.split()[0]
                                            )

                                        if rotation_val < 0:
                                            rotation_val = 360 + rotation_val
                                        self.rotation = int(rotation_val)
                                    except (ValueError, IndexError):
                                        pass
                    break

        except Exception as e:
            raise ValueError(f"Failed to probe {file_path}: {str(e)}")


class ImageItem(MediaItem):
    def __init__(self, file_path):
        super().__init__(file_path, is_image=True)
        self.width = 0
        self.height = 0

        try:
            # Use subprocess for stability with ffprobe
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed: {result.stderr}")

            probe = json.loads(result.stdout)

            for stream in probe["streams"]:
                if stream["codec_type"] == "video":
                    self.width = int(stream.get("width", 0))
                    self.height = int(stream.get("height", 0))
                    break

            # For images, duration is the display duration
            self.duration = self.display_duration
            self.end_time = self.display_duration

        except Exception as e:
            raise ValueError(f"Failed to probe image {file_path}: {str(e)}")


class ImageDurationDialog(QDialog):
    """Dialog to set image display duration"""

    def __init__(self, parent=None, current_duration=5.0):
        super().__init__(parent)
        self.setWindowTitle("Image Duration")
        self.setFixedWidth(300)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        info_label = QLabel("How long should images be displayed?")
        info_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(info_label)

        self.duration_spin = QSpinBox(self)
        self.duration_spin.setRange(1, 60)
        self.duration_spin.setValue(int(current_duration))
        self.duration_spin.setSuffix(" seconds")
        self.duration_spin.setFixedHeight(30)
        layout.addWidget(self.duration_spin)

        self.apply_to_all = QCheckBox("Apply to all images", self)
        self.apply_to_all.setChecked(True)
        layout.addWidget(self.apply_to_all)

        buttons_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK", self)
        self.ok_button.setFixedHeight(30)
        self.ok_button.clicked.connect(self.accept)

        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setFixedHeight(30)
        self.cancel_button.clicked.connect(self.reject)

        buttons_layout.addWidget(self.cancel_button)
        buttons_layout.addWidget(self.ok_button)
        layout.addLayout(buttons_layout)


class EditDialog(QDialog):
    """Dialog for editing media properties"""

    def __init__(self, parent=None, media_item=None):
        super().__init__(parent)
        self.media_item = media_item
        self.setWindowTitle("Edit Media Properties")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)

        # Create tabs for different edit options
        tab_widget = QTabWidget()

        # Trim tab for videos
        if not media_item.is_image:
            trim_tab = QWidget()
            trim_layout = QVBoxLayout(trim_tab)

            # Start time
            start_layout = QHBoxLayout()
            self.start_label = QLabel(f"Start: {media_item.start_time:.2f} sec")
            self.start_slider = QSlider(Qt.Horizontal)
            self.start_slider.setRange(0, int(media_item.duration * 1000))
            self.start_slider.setValue(int(media_item.start_time * 1000))
            self.start_slider.valueChanged.connect(self.update_start_time)
            start_layout.addWidget(self.start_label)
            start_layout.addWidget(self.start_slider, 1)
            trim_layout.addLayout(start_layout)

            # End time
            end_layout = QHBoxLayout()
            self.end_label = QLabel(
                f"End: {media_item.end_time if media_item.end_time else media_item.duration:.2f} sec"
            )
            self.end_slider = QSlider(Qt.Horizontal)
            self.end_slider.setRange(0, int(media_item.duration * 1000))
            self.end_slider.setValue(
                int((media_item.end_time or media_item.duration) * 1000)
            )
            self.end_slider.valueChanged.connect(self.update_end_time)
            end_layout.addWidget(self.end_label)
            end_layout.addWidget(self.end_slider, 1)
            trim_layout.addLayout(end_layout)

            tab_widget.addTab(trim_tab, "Trim")

        # Duration tab for images
        if media_item.is_image:
            duration_tab = QWidget()
            duration_layout = QVBoxLayout(duration_tab)

            duration_layout.addWidget(QLabel("Duration:"))
            self.duration_slider = QSlider(Qt.Horizontal)
            self.duration_slider.setRange(1, 30)
            self.duration_slider.setValue(int(media_item.display_duration))
            self.duration_label = QLabel(f"{media_item.display_duration:.1f} seconds")
            self.duration_slider.valueChanged.connect(self.update_duration)

            duration_layout.addWidget(self.duration_slider)
            duration_layout.addWidget(self.duration_label)

            tab_widget.addTab(duration_tab, "Duration")

        # Rotation tab for all media
        rotation_tab = QWidget()
        rotation_layout = QVBoxLayout(rotation_tab)

        rotation_layout.addWidget(QLabel("Rotate:"))
        self.rotation_buttons = QHBoxLayout()

        self.rotation_0_btn = QPushButton("0°")
        self.rotation_90_btn = QPushButton("90°")
        self.rotation_180_btn = QPushButton("180°")
        self.rotation_270_btn = QPushButton("270°")

        self.rotation_0_btn.clicked.connect(lambda: self.set_rotation(0))
        self.rotation_90_btn.clicked.connect(lambda: self.set_rotation(90))
        self.rotation_180_btn.clicked.connect(lambda: self.set_rotation(180))
        self.rotation_270_btn.clicked.connect(lambda: self.set_rotation(270))

        self.rotation_buttons.addWidget(self.rotation_0_btn)
        self.rotation_buttons.addWidget(self.rotation_90_btn)
        self.rotation_buttons.addWidget(self.rotation_180_btn)
        self.rotation_buttons.addWidget(self.rotation_270_btn)

        rotation_layout.addLayout(self.rotation_buttons)
        rotation_layout.addStretch()

        tab_widget.addTab(rotation_tab, "Rotation")

        # Effects tab
        effects_tab = QWidget()
        effects_layout = QVBoxLayout(effects_tab)

        effects_layout.addWidget(QLabel("Special Effects:"))
        effects_button = QPushButton("Edit Effects")
        effects_button.clicked.connect(self.edit_effects)

        # Display current speed if not default
        speed_text = ""
        if hasattr(media_item, "playback_speed") and media_item.playback_speed != 1.0:
            speed_text = f" - Speed: {media_item.playback_speed:.2f}x"

        # Display filter count if any
        filter_count = sum(
            1 for e in getattr(media_item, "effects", []) if e.effect_type == "filter"
        )
        filters_text = f" - Filters: {filter_count}" if filter_count > 0 else ""

        # Status label
        self.effects_status = QLabel(f"Current effects{speed_text}{filters_text}")

        effects_layout.addWidget(effects_button)
        effects_layout.addWidget(self.effects_status)
        effects_layout.addStretch()

        tab_widget.addTab(effects_tab, "Effects")

        # Highlight current rotation
        self.highlight_rotation(media_item.manual_rotation)

        layout.addWidget(tab_widget)

        # Buttons
        buttons = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        buttons.addWidget(cancel_button)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

    def update_start_time(self, value):
        """Update start time slider"""
        start_time = value / 1000.0
        end_time = self.media_item.end_time or self.media_item.duration

        # Make sure start time is valid
        if start_time >= end_time:
            start_time = end_time - 0.1
            self.start_slider.setValue(int(start_time * 1000))

        self.media_item.start_time = start_time
        self.start_label.setText(f"Start: {start_time:.2f} sec")

    def update_end_time(self, value):
        """Update end time slider"""
        end_time = value / 1000.0

        # Make sure end time is valid
        if end_time <= self.media_item.start_time:
            end_time = self.media_item.start_time + 0.1
            self.end_slider.setValue(int(end_time * 1000))

        self.media_item.end_time = end_time
        self.end_label.setText(f"End: {end_time:.2f} sec")

    def update_duration(self, value):
        """Update image duration"""
        self.media_item.display_duration = value
        self.media_item.duration = value
        self.media_item.end_time = value
        self.duration_label.setText(f"{value:.1f} seconds")

    def set_rotation(self, degrees):
        """Set rotation value"""
        self.media_item.manual_rotation = degrees
        self.highlight_rotation(degrees)

    def highlight_rotation(self, degrees):
        """Highlight the selected rotation button"""
        buttons = [
            self.rotation_0_btn,
            self.rotation_90_btn,
            self.rotation_180_btn,
            self.rotation_270_btn,
        ]

        for btn in buttons:
            btn.setStyleSheet("")

        if degrees == 0:
            self.rotation_0_btn.setStyleSheet(
                "background-color: #1abc9c; color: white;"
            )
        elif degrees == 90:
            self.rotation_90_btn.setStyleSheet(
                "background-color: #1abc9c; color: white;"
            )
        elif degrees == 180:
            self.rotation_180_btn.setStyleSheet(
                "background-color: #1abc9c; color: white;"
            )
        elif degrees == 270:
            self.rotation_270_btn.setStyleSheet(
                "background-color: #1abc9c; color: white;"
            )

    def edit_effects(self):
        """Open the effects dialog"""
        dialog = EffectsDialog(self, self.media_item)
        if dialog.exec_():
            # Update effects status label
            speed_text = ""
            if self.media_item.playback_speed != 1.0:
                speed_text = f" - Speed: {self.media_item.playback_speed:.2f}x"

            # Count filters
            filter_count = sum(
                1 for e in self.media_item.effects if e.effect_type == "filter"
            )
            filters_text = f" - Filters: {filter_count}" if filter_count > 0 else ""

            self.effects_status.setText(f"Current effects{speed_text}{filters_text}")


class ProcessingWorker(QObject):
    """Worker object for video processing"""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)  # task, result
    error = pyqtSignal(str, str)  # task, error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._abort = False
        self.music_file = None
        self.music_volume = 0.7  # Default 70% volume
        self.music_tracks = []  # List of MusicTrack objects

    def abort(self):
        """Signal the worker to abort processing"""
        self._abort = True

    def create_preview(self, media_item):
        """Create a preview for a single item"""
        try:
            # Check if we should abort
            if self._abort:
                return "Aborted"

            # Get or create preview filename
            preview_file = media_item.get_preview_filename()

            # If preview already exists, return it
            if media_item.preview_file and os.path.exists(media_item.preview_file):
                if os.path.getsize(media_item.preview_file) > 1000:  # Size sanity check
                    media_item.preview_status = "ready"
                    media_item.has_pending_changes = False
                    return media_item.preview_file
                else:
                    # Invalid preview, recreate it
                    try:
                        os.unlink(media_item.preview_file)
                    except:
                        pass
                    media_item.preview_file = None
                    media_item.preview_status = "none"

            # Create preview directory if needed
            os.makedirs(os.path.dirname(preview_file), exist_ok=True)

            self.progress.emit(
                10, f"Processing {os.path.basename(media_item.file_path)}..."
            )

            # Get effects filters if any
            effects_filter = media_item.get_effects_filter_string()

            if media_item.is_image:
                # Build filter string for image
                vf = "scale=480:-2"

                # Add rotation if needed
                if media_item.manual_rotation != 0:
                    rotation = f"rotate={media_item.manual_rotation*math.pi/180}"
                    vf = f"{rotation},{vf}"

                # Add effects if any
                if effects_filter:
                    vf = f"{effects_filter},{vf}"

                # Process image preview - low resolution for speed
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    media_item.file_path,
                    "-t",
                    str(media_item.display_duration),
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "30",
                    "-pix_fmt",
                    "yuv420p",
                    preview_file,
                ]
            else:
                # Build filter string for video
                vf = "scale=480:-2,fps=24"

                # Add rotation if needed
                total_rotation = (
                    media_item.rotation + media_item.manual_rotation
                ) % 360
                if total_rotation != 0:
                    if total_rotation == 90:
                        rotation_filter = "transpose=1"
                    elif total_rotation == 180:
                        rotation_filter = "transpose=2,transpose=2"
                    elif total_rotation == 270:
                        rotation_filter = "transpose=2"
                    vf = f"{rotation_filter},{vf}"

                # Add effects if any
                if effects_filter:
                    vf = f"{effects_filter},{vf}"

                # Process video preview - optimize for speed
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    media_item.file_path,
                    "-ss",
                    str(media_item.start_time),
                    "-t",
                    str(
                        (media_item.end_time or media_item.duration)
                        - media_item.start_time
                    ),
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "30",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-pix_fmt",
                    "yuv420p",
                    preview_file,
                ]

            # Run ffmpeg process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )

            # Process output line by line for progress tracking
            for line in iter(process.stdout.readline, ""):
                if self._abort:
                    process.terminate()
                    media_item.preview_status = "none"
                    return "Aborted"

                if "time=" in line:
                    # Try to extract time information
                    try:
                        time_parts = line.split("time=")[1].split()[0].split(":")
                        if len(time_parts) == 3:
                            hours, minutes, seconds = time_parts
                            current_sec = (
                                float(hours) * 3600
                                + float(minutes) * 60
                                + float(seconds.replace(",", "."))
                            )
                            total_sec = (
                                media_item.end_time or media_item.duration
                            ) - media_item.start_time
                            if media_item.is_image:
                                total_sec = media_item.display_duration
                            if total_sec > 0:
                                progress = min(
                                    int((current_sec / total_sec) * 80) + 10, 90
                                )
                                self.progress.emit(
                                    progress,
                                    f"Processing {os.path.basename(media_item.file_path)}...",
                                )
                    except:
                        pass

            # Wait for process to complete
            process.wait()

            if process.returncode != 0:
                self.progress.emit(0, "Error processing file")
                media_item.preview_status = "error"
                return f"Error: ffmpeg process failed with code {process.returncode}"

            self.progress.emit(100, "Preview ready")

            # Check if output file exists and is valid
            if os.path.exists(preview_file) and os.path.getsize(preview_file) > 1000:
                media_item.preview_file = preview_file
                media_item.preview_status = "ready"
                media_item.has_pending_changes = False
                return preview_file
            else:
                media_item.preview_status = "error"
                return "Error: Created preview file is invalid"

        except Exception as e:
            self.progress.emit(0, f"Error: {str(e)}")
            media_item.preview_status = "error"
            return f"Error: {str(e)}"

    def slider_released(self):
        """Handle slider release event"""
        self.position_slider_being_dragged = False
        self.set_position(self.position_slider.value())  # Only set position on release

    def update_progress(self, value, message):
        """Update progress dialog"""
        if self.progress_dialog is not None:
            try:
                self.progress_dialog.setValue(value)
                self.progress_dialog.setLabelText(message)
            except Exception as e:
                print(f"Progress update error: {e}")
        else:
            print(f"Progress update skipped: No active dialog for '{message}'")

    def process_all_clips(self, items):
        """Process all clips for preview - optimized for speed with HW acceleration"""
        try:
            if not items:
                return "No items to process"

            # Use libx264 for maximum compatibility
            best_encoder = "libx264"

            self.progress.emit(5, f"Using CPU encoding for maximum compatibility")

            # Process each item
            valid_files = []
            total_items = len(items)
            total_duration = 0

            for i, media_item in enumerate(items):
                if self._abort:
                    # Clean up any temp files
                    for temp_file in valid_files:
                        try:
                            if os.path.exists(temp_file):
                                os.unlink(temp_file)
                        except Exception as e:
                            print(f"Warning: Failed to clean up temp file: {e}")
                    return "Aborted"

                self.progress.emit(
                    int((i / total_items) * 70) + 5,
                    f"Processing item {i+1}/{total_items}...",
                )

                # Use the full duration as specified by user edits
                if not media_item.is_image:
                    # For video - use full edited duration
                    preview_duration = (
                        media_item.end_time or media_item.duration
                    ) - media_item.start_time
                else:
                    # For images - use full display duration
                    preview_duration = media_item.display_duration

                # Add to total duration
                total_duration += preview_duration

                # Create a temporary preview for this item
                temp_preview = os.path.join(
                    TEMP_DIR, f"temp_preview_{i}_{uuid.uuid4().hex[:8]}.mp4"
                )

                # Get effects filter
                effects_filter = media_item.get_effects_filter_string()

                # Base command for both image and video
                base_cmd = ["ffmpeg", "-y", "-v", "error"]

                # For images
                if media_item.is_image:
                    # Build the filter string
                    vf = "scale=480:-2,fps=24"

                    # Add rotation if needed
                    if media_item.manual_rotation != 0:
                        rotation = f"rotate={media_item.manual_rotation*math.pi/180}"
                        vf = f"{rotation},{vf}"

                    # Add effects if any
                    if effects_filter:
                        vf = f"{effects_filter},{vf}"

                    # Build command
                    cmd = base_cmd + [
                        "-loop",
                        "1",
                        "-i",
                        media_item.file_path,
                        "-t",
                        str(preview_duration),
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-crf",
                        "28",
                        "-pix_fmt",
                        "yuv420p",
                        "-f",
                        "mp4",
                        temp_preview,
                    ]
                else:
                    # For videos - build the filter string
                    vf = "scale=480:-2,fps=24"

                    # Add rotation if needed
                    total_rotation = (
                        media_item.rotation + media_item.manual_rotation
                    ) % 360
                    if total_rotation != 0:
                        rotation_filter = ""
                        if total_rotation == 90:
                            rotation_filter = "transpose=1,"
                        elif total_rotation == 180:
                            rotation_filter = "transpose=2,transpose=2,"
                        elif total_rotation == 270:
                            rotation_filter = "transpose=2,"
                        vf = f"{rotation_filter}{vf}"

                    # Add effects if any
                    if effects_filter:
                        vf = f"{effects_filter},{vf}"

                    # Build command
                    cmd = base_cmd + [
                        "-ss",
                        str(media_item.start_time),
                        "-i",
                        media_item.file_path,
                        "-t",
                        str(preview_duration),
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-crf",
                        "28",
                        "-c:a",
                        "aac",  # Include audio for a proper preview
                        "-b:a",
                        "96k",  # Lower audio bitrate for faster processing
                        "-pix_fmt",
                        "yuv420p",
                        "-f",
                        "mp4",
                        temp_preview,
                    ]

                # Run command with process monitoring to allow cancellation
                try:
                    self.progress.emit(
                        int((i / total_items) * 60) + 10,
                        f"Processing {os.path.basename(media_item.file_path)}...",
                    )

                    process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    )

                    # Monitor the process with timeout
                    start_time = time.time()
                    max_processing_time = max(
                        60, preview_duration * 2
                    )  # Allow more time for longer clips

                    while process.poll() is None:
                        # Check for abort
                        if self._abort:
                            process.terminate()
                            try:
                                if os.path.exists(temp_preview):
                                    os.unlink(temp_preview)
                            except:
                                pass
                            # Clean up existing files
                            for file in valid_files:
                                try:
                                    if os.path.exists(file):
                                        os.unlink(file)
                                except:
                                    pass
                            return "Aborted"

                        # Check for timeout
                        if time.time() - start_time > max_processing_time:
                            process.terminate()
                            print(
                                f"Processing timeout for {media_item.file_path} after {max_processing_time} seconds"
                            )
                            break

                        # Wait a bit before checking again
                        time.sleep(0.1)

                    # Get any error output
                    _, stderr = process.communicate(timeout=1)

                    # Check if file was created successfully
                    if (
                        os.path.exists(temp_preview)
                        and os.path.getsize(temp_preview) > 1000
                    ):
                        valid_files.append(temp_preview)
                        # Mark the item as having no pending changes
                        media_item.has_pending_changes = False
                    else:
                        print(
                            f"Error creating preview for {media_item.file_path}: {stderr}"
                        )
                except Exception as e:
                    print(f"Error processing file {media_item.file_path}: {str(e)}")
                    # Skip this file on any error
                    pass

            if not valid_files:
                return "Failed to create any valid previews"

            # Handle case with only one valid file - just return it directly
            if len(valid_files) == 1:
                output_file = os.path.join(
                    TEMP_DIR, f"preview_all_{uuid.uuid4().hex}.mp4"
                )

                # Check if music should be added to single file
                if self.music_tracks or (
                    self.music_file and os.path.exists(self.music_file)
                ):
                    self.progress.emit(80, "Adding background music...")

                    # Use music tracks if available, otherwise use single music file
                    if self.music_tracks:
                        try:
                            # Copy the file first - safer approach
                            shutil.copy(valid_files[0], output_file)

                            # For a single video, use a simpler approach to add music
                            # This reduces the chance of a segfault
                            temp_music_file = os.path.join(
                                TEMP_DIR, f"temp_music_{uuid.uuid4().hex[:8]}.mp3"
                            )

                            # First, create a combined music file if multiple tracks
                            if len(self.music_tracks) > 1:
                                # Build command to mix music tracks
                                music_cmd = ["ffmpeg", "-y", "-v", "error"]
                                music_filter = ""

                                # Add inputs
                                valid_track_count = 0
                                for track in self.music_tracks:
                                    if os.path.exists(track.file_path):
                                        music_cmd.extend(["-i", track.file_path])
                                        valid_track_count += 1

                                if valid_track_count == 0:
                                    # No valid music tracks, just return video
                                    return (output_file, total_duration)

                                # Create filter for mixing
                                for i in range(valid_track_count):
                                    music_filter += (
                                        f"[{i}:a]volume={self.music_tracks[i].volume},"
                                    )

                                    # Apply trim if needed
                                    if (
                                        self.music_tracks[i].start_time_in_track > 0
                                        or self.music_tracks[i].duration
                                    ):
                                        music_filter += f"atrim=start={self.music_tracks[i].start_time_in_track}"
                                        if self.music_tracks[i].duration:
                                            music_filter += f":duration={self.music_tracks[i].duration}"
                                        music_filter += ","

                                    # End this input's processing
                                    music_filter += f"aformat=sample_fmts=fltp[a{i}];"

                                # Mix all inputs
                                music_filter += "".join(
                                    f"[a{i}]" for i in range(valid_track_count)
                                )
                                music_filter += f"amix=inputs={valid_track_count}:duration=longest[aout]"

                                # Finalize command
                                music_cmd.extend(
                                    [
                                        "-filter_complex",
                                        music_filter,
                                        "-map",
                                        "[aout]",
                                        "-c:a",
                                        "mp3",
                                        "-b:a",
                                        "192k",
                                        temp_music_file,
                                    ]
                                )

                                # Run music mix command with timeout
                                try:
                                    music_process = subprocess.Popen(
                                        music_cmd,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        text=True,
                                    )

                                    # Wait with timeout
                                    start_time = time.time()
                                    while music_process.poll() is None:
                                        if (
                                            time.time() - start_time > 60
                                        ):  # 60 second timeout
                                            music_process.terminate()
                                            print("Music mixing timed out")
                                            break
                                        time.sleep(0.1)

                                    # Check result
                                    if (
                                        not os.path.exists(temp_music_file)
                                        or os.path.getsize(temp_music_file) < 1000
                                    ):
                                        print("Failed to create mixed music file")
                                        # Return video without music
                                        return (output_file, total_duration)
                                except Exception as e:
                                    print(f"Error mixing music tracks: {str(e)}")
                                    # Return video without music
                                    return (output_file, total_duration)
                            else:
                                # Just one track, use it directly
                                if os.path.exists(self.music_tracks[0].file_path):
                                    temp_music_file = self.music_tracks[0].file_path
                                else:
                                    # No valid music track
                                    return (output_file, total_duration)

                            # Now add the music to the video
                            music_add_cmd = [
                                "ffmpeg",
                                "-y",
                                "-v",
                                "error",
                                "-i",
                                output_file,
                                "-i",
                                temp_music_file,
                                "-filter_complex",
                                "[1:a]volume=0.7[music];[0:a][music]amix=inputs=2:duration=first[a]",
                                "-map",
                                "0:v",
                                "-map",
                                "[a]",
                                "-c:v",
                                "copy",
                                "-c:a",
                                "aac",
                                "-b:a",
                                "128k",
                                "-shortest",
                            ]

                            # Create final output file
                            final_output = os.path.join(
                                TEMP_DIR, f"preview_all_music_{uuid.uuid4().hex}.mp4"
                            )
                            music_add_cmd.append(final_output)

                            # Run command with timeout
                            try:
                                add_process = subprocess.Popen(
                                    music_add_cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                )

                                # Wait with timeout
                                start_time = time.time()
                                while add_process.poll() is None:
                                    if (
                                        time.time() - start_time > 60
                                    ):  # 60 second timeout
                                        add_process.terminate()
                                        print("Music addition timed out")
                                        break
                                    time.sleep(0.1)

                                # Check result
                                if (
                                    os.path.exists(final_output)
                                    and os.path.getsize(final_output) > 1000
                                ):
                                    # Success - clean up and return
                                    os.unlink(
                                        output_file
                                    )  # Remove the non-music version

                                    # Clean up temp music file if we created it
                                    if (
                                        len(self.music_tracks) > 1
                                        and temp_music_file
                                        != self.music_tracks[0].file_path
                                    ):
                                        try:
                                            os.unlink(temp_music_file)
                                        except:
                                            pass

                                    # Clean up temp files
                                    for file in valid_files:
                                        try:
                                            if os.path.exists(file):
                                                os.unlink(file)
                                        except:
                                            pass

                                    self.progress.emit(
                                        100, "Preview ready (with music)"
                                    )
                                    return (final_output, total_duration)
                            except Exception as e:
                                print(f"Error adding music to video: {str(e)}")
                        except Exception as e:
                            print(f"Error in music processing: {str(e)}")
                            # Fall through to use the video without music
                    else:
                        # Legacy: Add music to single file using the simple approach
                        try:
                            # Copy file first
                            shutil.copy(valid_files[0], output_file)

                            # Create music command
                            music_cmd = [
                                "ffmpeg",
                                "-y",
                                "-v",
                                "error",
                                "-i",
                                output_file,
                                "-i",
                                self.music_file,
                                "-filter_complex",
                                f"[1:a]volume={self.music_volume}[music];[0:a][music]amix=inputs=2:duration=first[a]",
                                "-map",
                                "0:v",
                                "-map",
                                "[a]",
                                "-c:v",
                                "copy",
                                "-c:a",
                                "aac",
                                "-b:a",
                                "128k",
                            ]

                            # Create final output
                            final_output = os.path.join(
                                TEMP_DIR, f"preview_all_music_{uuid.uuid4().hex}.mp4"
                            )
                            music_cmd.append(final_output)

                            # Run command
                            process = subprocess.Popen(
                                music_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                            )

                            # Monitor with timeout
                            start_time = time.time()
                            while process.poll() is None:
                                if self._abort:
                                    process.terminate()
                                    try:
                                        if os.path.exists(final_output):
                                            os.unlink(final_output)
                                    except:
                                        pass
                                    return "Aborted"

                                if time.time() - start_time > 60:
                                    process.terminate()
                                    break

                                time.sleep(0.1)

                            # Check if successful
                            if (
                                os.path.exists(final_output)
                                and os.path.getsize(final_output) > 1000
                            ):
                                # Success - clean up and return
                                os.unlink(output_file)  # Remove non-music version

                                # Clean up temp files
                                for file in valid_files:
                                    try:
                                        if os.path.exists(file):
                                            os.unlink(file)
                                    except:
                                        pass

                                self.progress.emit(100, "Preview ready (with music)")
                                return (final_output, total_duration)
                        except Exception as e:
                            print(f"Error in legacy music processing: {str(e)}")
                            # Fall through to use video without music

                # If we get here, either music wasn't requested or it failed
                # Just return the copy of the single clip
                try:
                    # Make sure we have a copy of the file
                    if (
                        not os.path.exists(output_file)
                        or os.path.getsize(output_file) < 1000
                    ):
                        shutil.copy(valid_files[0], output_file)

                    if (
                        os.path.exists(output_file)
                        and os.path.getsize(output_file) > 1000
                    ):
                        self.progress.emit(100, "Preview ready (single clip)")

                        # Clean up temp files
                        for file in valid_files:
                            try:
                                if os.path.exists(file) and file != output_file:
                                    os.unlink(file)
                            except:
                                pass

                        return (output_file, total_duration)
                except Exception as e:
                    print(f"Error copying single file: {str(e)}")
                    # Try to return the original file as fallback
                    if os.path.exists(valid_files[0]):
                        return (valid_files[0], total_duration)
                    return "Error: Failed to create preview output"

            # Multiple clips - concatenate them
            self.progress.emit(80, "Combining all clips...")

            # Output file
            output_file = os.path.join(TEMP_DIR, f"preview_all_{uuid.uuid4().hex}.mp4")

            # Create a temporary file list for concat
            file_list = os.path.join(TEMP_DIR, f"files_{uuid.uuid4().hex}.txt")
            with open(file_list, "w") as f:
                for file_path in valid_files:
                    fixed_path = file_path.replace("\\", "/")
                    f.write(f"file '{fixed_path}'\n")

            # Check for cancellation
            if self._abort:
                # Clean up
                try:
                    if os.path.exists(file_list):
                        os.unlink(file_list)
                    for file in valid_files:
                        if os.path.exists(file):
                            os.unlink(file)
                except:
                    pass
                return "Aborted"

            # Try to use the concat demuxer first (faster)
            cmd = [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                file_list,
                "-c",
                "copy",
                output_file,
            ]

            # Run with monitoring
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            # Monitor the process
            start_time = time.time()
            while process.poll() is None:
                # Check for abort
                if self._abort:
                    process.terminate()

                    # Clean up
                    try:
                        if os.path.exists(file_list):
                            os.unlink(file_list)
                        if os.path.exists(output_file):
                            os.unlink(output_file)
                        for file in valid_files:
                            if os.path.exists(file):
                                os.unlink(file)
                    except:
                        pass
                    return "Aborted"

                # Check for timeout (longer for concat)
                if time.time() - start_time > 60:  # 60 seconds timeout
                    process.terminate()
                    print("Concat operation timed out after 60 seconds")
                    break

                # Wait a bit
                time.sleep(0.1)

            # Clean up file list
            if os.path.exists(file_list):
                os.unlink(file_list)

            # Add background music if provided
            if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                # Check if we have music tracks or legacy music file
                if self.music_tracks or (
                    self.music_file and os.path.exists(self.music_file)
                ):
                    self.progress.emit(90, "Adding background music...")
                    # Use a simpler approach for adding music to avoid segfault

                    try:
                        # Create a combined music file if needed
                        temp_music_file = os.path.join(
                            TEMP_DIR, f"temp_music_{uuid.uuid4().hex[:8]}.mp3"
                        )

                        if self.music_tracks and len(self.music_tracks) > 0:
                            # If multiple tracks, mix them first
                            if len(self.music_tracks) > 1:
                                # Mix all music tracks to a single file
                                music_cmd = ["ffmpeg", "-y", "-v", "error"]
                                music_inputs = []

                                # Add all valid tracks
                                valid_tracks = []
                                for track in self.music_tracks:
                                    if os.path.exists(track.file_path):
                                        music_cmd.extend(["-i", track.file_path])
                                        valid_tracks.append(track)

                                if not valid_tracks:
                                    # No valid music, skip music addition
                                    self.progress.emit(
                                        100, "Preview ready (no music available)"
                                    )
                                    return (output_file, total_duration)

                                # Create filter string
                                filter_str = ""
                                for i, track in enumerate(valid_tracks):
                                    # Base volume adjustment
                                    filter_str += f"[{i}:a]volume={track.volume}"

                                    # Add trim if needed
                                    if track.start_time_in_track > 0 or track.duration:
                                        filter_str += (
                                            f",atrim=start={track.start_time_in_track}"
                                        )
                                        if track.duration:
                                            filter_str += f":duration={track.duration}"

                                    # Add delay for start_time_in_compilation
                                    if track.start_time_in_compilation > 0:
                                        filter_str += f",adelay={int(track.start_time_in_compilation*1000)}|{int(track.start_time_in_compilation*1000)}"

                                    # End this input
                                    filter_str += f"[a{i}];"

                                # Mix all inputs
                                filter_str += "".join(
                                    f"[a{i}]" for i in range(len(valid_tracks))
                                )
                                filter_str += f"amix=inputs={len(valid_tracks)}:duration=longest[aout]"

                                # Complete command
                                music_cmd.extend(
                                    [
                                        "-filter_complex",
                                        filter_str,
                                        "-map",
                                        "[aout]",
                                        "-c:a",
                                        "mp3",
                                        "-b:a",
                                        "192k",
                                        temp_music_file,
                                    ]
                                )

                                # Run with timeout
                                music_process = subprocess.Popen(
                                    music_cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                )

                                start_time = time.time()
                                while music_process.poll() is None:
                                    if time.time() - start_time > 60:
                                        music_process.terminate()
                                        print("Music mixing timed out")
                                        # Skip music addition
                                        self.progress.emit(
                                            100, "Preview ready (music mixing failed)"
                                        )
                                        return (output_file, total_duration)
                                    time.sleep(0.1)

                                # Check result
                                if (
                                    not os.path.exists(temp_music_file)
                                    or os.path.getsize(temp_music_file) < 1000
                                ):
                                    # Failed to create mixed music
                                    self.progress.emit(100, "Preview ready (no music)")
                                    return (output_file, total_duration)
                            else:
                                # Just one track, use it directly
                                temp_music_file = self.music_tracks[0].file_path
                        elif self.music_file and os.path.exists(self.music_file):
                            # Use legacy music file directly
                            temp_music_file = self.music_file
                        else:
                            # No valid music
                            self.progress.emit(100, "Preview ready (no music)")
                            return (output_file, total_duration)

                        # Now add the music to the video
                        final_output = os.path.join(
                            TEMP_DIR, f"preview_all_music_{uuid.uuid4().hex}.mp4"
                        )

                        # Simple filter for adding music
                        volume = self.music_volume
                        if self.music_tracks and len(self.music_tracks) > 0:
                            volume = 0.7  # Default if we mixed tracks

                        add_cmd = [
                            "ffmpeg",
                            "-y",
                            "-v",
                            "error",
                            "-i",
                            output_file,
                            "-i",
                            temp_music_file,
                            "-filter_complex",
                            f"[1:a]volume={volume}[music];[0:a][music]amix=inputs=2:duration=first[a]",
                            "-map",
                            "0:v",
                            "-map",
                            "[a]",
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "128k",
                            "-shortest",
                            final_output,
                        ]

                        # Run with timeout
                        add_process = subprocess.Popen(
                            add_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        start_time = time.time()
                        while add_process.poll() is None:
                            if self._abort:
                                add_process.terminate()
                                try:
                                    if os.path.exists(final_output):
                                        os.unlink(final_output)
                                except:
                                    pass
                                return "Aborted"

                            if time.time() - start_time > 60:
                                add_process.terminate()
                                print("Music addition timed out")
                                break

                            time.sleep(0.1)

                        # Check result
                        if (
                            os.path.exists(final_output)
                            and os.path.getsize(final_output) > 1000
                        ):
                            # Success! Use the music version
                            try:
                                # Remove original
                                if os.path.exists(output_file):
                                    os.unlink(output_file)

                                # Clean up temp music file if we created it
                                if (
                                    self.music_tracks
                                    and len(self.music_tracks) > 1
                                    and temp_music_file != self.music_file
                                ):
                                    try:
                                        os.unlink(temp_music_file)
                                    except:
                                        pass

                                # Use the music version
                                output_file = final_output
                            except Exception as e:
                                print(f"Error finalizing music version: {e}")
                                # If we can't clean up, just use what we have
                                if os.path.exists(final_output):
                                    output_file = final_output
                        else:
                            # Music addition failed, but we still have the original
                            print("Failed to add music, using version without music")
                    except Exception as e:
                        print(f"Error in music processing: {e}")
                        # Continue with no music

            # Check if concat succeeded
            if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                self.progress.emit(
                    100, "Preview ready" + (" with music" if self.music_file else "")
                )
                # Clean up individual files
                for file in valid_files:
                    try:
                        if os.path.exists(file) and file != output_file:
                            os.unlink(file)
                    except:
                        pass
                return (output_file, total_duration)
            else:
                # Try one last approach: just return the first clip
                fallback_file = os.path.join(
                    TEMP_DIR, f"fallback_preview_{uuid.uuid4().hex}.mp4"
                )
                try:
                    shutil.copy(valid_files[0], fallback_file)
                    self.progress.emit(100, "Preview ready (single clip fallback)")
                    return (fallback_file, total_duration)
                except Exception as e:
                    print(f"Final fallback failed: {str(e)}")
                    # Return one of the original files if possible
                    if valid_files and os.path.exists(valid_files[0]):
                        return (valid_files[0], total_duration)
                    return "Error: Failed to create combined preview"

        except Exception as e:
            print(f"Error in process_all_clips: {str(e)}")
            return f"Error: {str(e)}"

    def export_video(self, items, output_path):
        """Export the final compilation video"""
        try:
            if not items:
                return "No items to process"

            self.progress.emit(5, "Starting export...")

            # Create a temporary directory for intermediate files
            export_temp = os.path.join(TEMP_DIR, f"export_{uuid.uuid4().hex}")
            os.makedirs(export_temp, exist_ok=True)

            # Process each item to create intermediate files
            temp_files = []
            total_items = len(items)

            # Get best available encoder for final export
            hw_encoders = check_hw_encoders()
            final_encoder = "libx264"  # Use software encoding for compatibility

            for i, media_item in enumerate(items):
                if self._abort:
                    # Clean up temp files
                    for file in temp_files:
                        try:
                            if os.path.exists(file):
                                os.unlink(file)
                        except:
                            pass

                    try:
                        shutil.rmtree(export_temp)
                    except:
                        pass

                    return "Aborted"

                # Update progress
                self.progress.emit(
                    int((i / total_items) * 60) + 5,
                    f"Processing item {i+1}/{total_items}...",
                )

                # Temp file for this item
                temp_file = os.path.join(export_temp, f"part_{i:04d}.mp4")

                # Get effects filters if any
                effects_filter = media_item.get_effects_filter_string()

                # Handle images vs videos
                if media_item.is_image:
                    # Image to video
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-loop",
                        "1",
                        "-i",
                        media_item.file_path,
                        "-t",
                        str(media_item.display_duration),
                    ]

                    # Build filter string
                    vf = "scale=-2:720"

                    # Add rotation if needed
                    if media_item.manual_rotation != 0:
                        rotation = f"rotate={media_item.manual_rotation*math.pi/180}"
                        vf = f"{rotation},{vf}"

                    # Add effects if any
                    if effects_filter:
                        vf = f"{effects_filter},{vf}"

                    # Add filter and output options
                    cmd.extend(
                        [
                            "-vf",
                            vf,
                            "-c:v",
                            "libx264",
                            "-preset",
                            "medium",
                            "-crf",
                            "22",
                            "-pix_fmt",
                            "yuv420p",
                            temp_file,
                        ]
                    )

                else:
                    # Video clip
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        str(media_item.start_time),
                        "-i",
                        media_item.file_path,
                        "-t",
                        str(
                            (media_item.end_time or media_item.duration)
                            - media_item.start_time
                        ),
                    ]

                    # Build filter string
                    vf = "scale=-2:720"

                    # Add rotation if needed
                    total_rotation = (
                        media_item.rotation + media_item.manual_rotation
                    ) % 360
                    if total_rotation != 0:
                        if total_rotation == 90:
                            rotation_filter = "transpose=1"
                        elif total_rotation == 180:
                            rotation_filter = "transpose=2,transpose=2"
                        elif total_rotation == 270:
                            rotation_filter = "transpose=2"
                        vf = f"{rotation_filter},{vf}"

                    # Add effects if any
                    if effects_filter:
                        vf = f"{effects_filter},{vf}"

                    # Add filter and output options
                    cmd.extend(
                        [
                            "-vf",
                            vf,
                            "-c:v",
                            "libx264",
                            "-preset",
                            "medium",
                            "-crf",
                            "22",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "128k",
                            "-pix_fmt",
                            "yuv420p",
                            temp_file,
                        ]
                    )

                # Run the command
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                )

                # Monitor process
                while process.poll() is None:
                    # Check for abort
                    if self._abort:
                        process.terminate()
                        # Clean up
                        for file in temp_files:
                            try:
                                if os.path.exists(file):
                                    os.unlink(file)
                            except:
                                pass

                        try:
                            shutil.rmtree(export_temp)
                        except:
                            pass

                        return "Aborted"

                    # Wait a bit to avoid busy waiting
                    time.sleep(0.1)

                # Check if file was created successfully
                if os.path.exists(temp_file) and os.path.getsize(temp_file) > 1000:
                    temp_files.append(temp_file)
                else:
                    stdout, stderr = process.communicate()
                    print(f"Error creating temp file for item {i}: {stderr}")
                    continue  # Skip this file

            # Check if we have any valid files
            if not temp_files:
                return "Error: No valid media files could be processed"

            # Create a file list for concatenation
            file_list = os.path.join(export_temp, "files.txt")
            with open(file_list, "w") as f:
                for temp_file in temp_files:
                    fixed_path = temp_file.replace("\\", "/")
                    f.write(f"file '{fixed_path}'\n")

            # Concatenate all the files
            self.progress.emit(70, "Combining all clips...")

            # First try fast concat
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                file_list,
                "-c",
                "copy",
                output_path,
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            # Monitor process
            while process.poll() is None:
                # Check for abort
                if self._abort:
                    process.terminate()

                    # Clean up
                    try:
                        if os.path.exists(output_path):
                            os.unlink(output_path)
                    except:
                        pass

                    for file in temp_files:
                        try:
                            if os.path.exists(file):
                                os.unlink(file)
                        except:
                            pass

                    try:
                        shutil.rmtree(export_temp)
                    except:
                        pass

                    return "Aborted"

                # Wait a bit to avoid busy waiting
                time.sleep(0.1)

            # Check if concat worked
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                # Add music if requested and exported successfully
                if self.music_tracks:
                    self.progress.emit(85, "Adding background music...")
                    output_with_music = os.path.join(
                        export_temp, f"export_music_{uuid.uuid4().hex}.mp4"
                    )

                    # Create a complex filter for multiple music tracks
                    music_filters = ""
                    music_mix = ""

                    # Build command base
                    cmd_parts = [
                        "ffmpeg",
                        "-y",
                        "-v",
                        "error",
                        "-i",
                        output_path,  # First input is the video
                    ]

                    # Add input for each music track
                    track_ids = []
                    for i, track in enumerate(self.music_tracks):
                        if not os.path.exists(track.file_path):
                            continue

                        # Add input for this track
                        cmd_parts.extend(["-i", track.file_path])
                        input_idx = i + 1  # Input index in ffmpeg (0 is video)
                        track_id = f"m{i}"
                        track_ids.append(track_id)

                        # Add volume and trim filter
                        music_filters += f"[{input_idx}:a]volume={track.volume}"

                        # Add trim if needed
                        if track.start_time_in_track > 0 or track.duration:
                            music_filters += f",atrim=start={track.start_time_in_track}"
                            if track.duration:
                                music_filters += f":duration={track.duration}"

                        # Add delay if needed (for start time in compilation)
                        if track.start_time_in_compilation > 0:
                            music_filters += f",adelay={int(track.start_time_in_compilation*1000)}|{int(track.start_time_in_compilation*1000)}"

                        # Name the output
                        music_filters += f"[{track_id}];"

                    # Combine all music tracks if there are multiple
                    if len(track_ids) > 1:
                        music_mix = (
                            "".join([f"[{tid}]" for tid in track_ids])
                            + f"amix=inputs={len(track_ids)}:duration=longest[music];"
                        )
                    elif len(track_ids) == 1:
                        music_mix = f"[{track_ids[0]}]aformat=sample_fmts=fltp[music];"

                    # Combine with original audio if we have music
                    if music_mix:
                        filter_complex = f"{music_filters}{music_mix}[0:a][music]amix=inputs=2:duration=first[a]"

                        # Complete command
                        cmd_parts.extend(
                            [
                                "-filter_complex",
                                filter_complex,
                                "-map",
                                "0:v",
                                "-map",
                                "[a]",
                                "-c:v",
                                "copy",
                                "-c:a",
                                "aac",
                                "-b:a",
                                "192k",
                                "-shortest",
                                output_with_music,
                            ]
                        )

                        process = subprocess.Popen(
                            cmd_parts,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        # Monitor with timeout
                        start_time = time.time()
                        while process.poll() is None:
                            if self._abort:
                                process.terminate()
                                try:
                                    if os.path.exists(output_with_music):
                                        os.unlink(output_with_music)
                                except:
                                    pass
                                return "Aborted"

                            # Wait a bit
                            time.sleep(0.1)

                        # Check if music addition succeeded
                        if (
                            os.path.exists(output_with_music)
                            and os.path.getsize(output_with_music) > 1000
                        ):
                            try:
                                # Replace the output file with music version
                                os.unlink(output_path)
                                shutil.move(output_with_music, output_path)
                            except Exception as e:
                                print(f"Error replacing output file: {str(e)}")

                # Clean up
                try:
                    shutil.rmtree(export_temp)
                except:
                    pass

                self.progress.emit(100, "Export complete")
                return output_path

            # If concat failed, try re-encoding
            self.progress.emit(80, "Using alternate export method...")

            # Create combined filter
            filter_complex = ""

            if len(temp_files) == 1:
                # Just one file, copy it with re-encoding
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    temp_files[0],
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "22",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-pix_fmt",
                    "yuv420p",
                    output_path,
                ]
            else:
                # Multiple files
                inputs = []
                for temp_file in temp_files:
                    inputs.extend(["-i", temp_file])

                # Create filter complex for concat
                for i in range(len(temp_files)):
                    filter_complex += f"[{i}:v]"
                filter_complex += f"concat=n={len(temp_files)}:v=1:a=0[outv];"

                # Add audio if available (from first file)
                audio_option = []
                for i, temp_file in enumerate(temp_files):
                    try:
                        audio_info = subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-select_streams",
                                "a",
                                "-show_streams",
                                "-of",
                                "json",
                                temp_file,
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        if '"codec_type":"audio"' in audio_info.stdout:
                            filter_complex += f"[{i}:a]aresample=44100[a{i}];"
                            audio_option.extend(["-map", f"[a{i}]"])
                            break
                    except:
                        pass

                # Add video map
                filter_complex += f"[outv]scale=-2:720[outv2]"

                # Build final command
                cmd = (
                    ["ffmpeg", "-y"]
                    + inputs
                    + ["-filter_complex", filter_complex, "-map", "[outv2]"]
                    + audio_option
                    + [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "medium",
                        "-crf",
                        "22",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-pix_fmt",
                        "yuv420p",
                        output_path,
                    ]
                )

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            # Monitor process
            while process.poll() is None:
                # Check for abort
                if self._abort:
                    process.terminate()

                    # Clean up
                    try:
                        if os.path.exists(output_path):
                            os.unlink(output_path)
                    except:
                        pass

                    for file in temp_files:
                        try:
                            if os.path.exists(file):
                                os.unlink(file)
                        except:
                            pass

                    try:
                        shutil.rmtree(export_temp)
                    except:
                        pass

                    return "Aborted"

                # Sleep a bit to avoid busy waiting
                time.sleep(0.1)

            # Clean up
            try:
                shutil.rmtree(export_temp)
            except:
                pass

            # Final check
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                self.progress.emit(100, "Export complete")
                return output_path
            else:
                stdout, stderr = process.communicate()
                print(f"Export failed: {stderr}")
                return "Error: Failed to create output file"

        except Exception as e:
            print(f"Export error: {str(e)}")
            return f"Error: {str(e)}"


class ProcessingThread(QThread):
    """Thread that runs video processing operations"""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)  # task, result
    error = pyqtSignal(str, str)  # task, error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = ProcessingWorker()
        self.worker.moveToThread(self)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.error.connect(self.on_worker_error)

        self.task = None
        self.args = None

    def run(self):
        try:
            if self.task == "preview_item":
                result = self.worker.create_preview(self.args[0])
                self.on_worker_finished(self.task, result)
            elif self.task == "preview_all":
                result = self.worker.process_all_clips(self.args[0])
                self.on_worker_finished(self.task, result)
            elif self.task == "export":
                result = self.worker.export_video(self.args[0], self.args[1])
                self.on_worker_finished(self.task, result)
            else:
                self.error.emit(self.task, "Unknown task")
        except Exception as e:
            self.error.emit(self.task, str(e))

    def on_worker_finished(self, task, result):
        """Handle worker completion"""
        self.finished.emit(self.task, result)

    def on_worker_error(self, task, error):
        """Handle worker error"""
        self.error.emit(self.task, error)

    def setup_task(self, task, args):
        """Set up the task to be run"""
        self.task = task
        self.args = args

    def abort(self):
        """Abort the current task"""
        self.worker.abort()


class TimelineWidget(QWidget):
    """Interactive widget to display the timeline of clips with drag and zoom support"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips = []
        self.total_duration = 0
        self.current_position = 0
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Scroll and zoom properties
        self.scroll_offset = -1  # In pixels
        self.zoom_level = 1.0  # Scale factor (1.0 = fit all clips)
        self.pixels_per_second = 50  # Base scale when zoom is 1.0
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_offset = 0
        self.hover_clip_index = -1
        self.hover_x = -1
        self.music_tracks = []  # List of music tracks to display
        self.has_pending_changes = False  # Flag to indicate unsaved changes

        # Set mouse tracking to handle hover effects
        self.setMouseTracking(True)

        # Ensure the widget can receive focus for key events
        self.setFocusPolicy(Qt.StrongFocus)

    def set_position(self, position):
        """Set the current playback position in seconds"""
        self.current_position = position

        # Auto-scroll to keep playback position visible
        if self.clips and self.total_duration > 0:
            pixels_per_second = self.pixels_per_second * self.zoom_level
            widget_width = self.width()

            # Convert current position to pixels
            position_x = position * pixels_per_second - self.scroll_offset

            # Check if position is outside visible area
            if position_x < 0 or position_x > widget_width:
                # Center the position in the viewport
                self.scroll_offset = max(
                    0, position * pixels_per_second - widget_width / 2
                )

        self.update()

    def set_clips(self, clips):
        """Set the clips to display"""
        self.clips = clips
        self.total_duration = sum(clip.get("duration", 0) for clip in clips)

        # Adjust zoom level to fit all clips if needed
        if self.total_duration > 0 and self.width() > 0:
            min_zoom = max(
                0.2, self.width() / (self.total_duration * self.pixels_per_second)
            )
            if self.zoom_level < min_zoom:
                self.zoom_level = min_zoom

        self.update()

    def set_music_tracks(self, tracks):
        """Set music tracks to display in timeline"""
        self.music_tracks = tracks
        self.update()

    def set_pending_changes(self, has_changes):
        """Set whether there are pending changes"""
        self.has_pending_changes = has_changes
        self.update()

    def timeline_width(self):
        """Calculate the total width of the timeline in pixels based on zoom"""
        return max(
            self.width(),
            int(self.total_duration * self.pixels_per_second * self.zoom_level),
        )

    def seconds_to_pixels(self, seconds):
        """Convert a time in seconds to pixels based on current zoom"""
        return seconds * self.pixels_per_second * self.zoom_level

    def pixels_to_seconds(self, pixels):
        """Convert pixels to seconds based on current zoom"""
        if self.pixels_per_second * self.zoom_level == 0:
            return 0
        return pixels / (self.pixels_per_second * self.zoom_level)

    def mousePressEvent(self, event):
        """Handle mouse press events for dragging"""
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_start_x = event.x()
            self.drag_start_offset = self.scroll_offset
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release events for dragging"""
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move events for dragging and hover effects"""
        if self.dragging:
            # Calculate drag distance
            delta_x = event.x() - self.drag_start_x
            new_offset = max(0, self.drag_start_offset - delta_x)

            # Limit scrolling to timeline width
            max_offset = max(0, self.timeline_width() - self.width())
            self.scroll_offset = min(new_offset, max_offset)

            self.update()
        else:
            # For hover effects, determine which clip is under the cursor
            self.hover_x = event.x()
            self.hover_clip_index = self.get_clip_at_position(
                int(event.x() + self.scroll_offset)
            )
            self.update()

        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        """Handle mouse wheel events for zooming"""
        if event.modifiers() & Qt.ControlModifier:
            # Zoom in/out
            zoom_delta = 0.1 if event.angleDelta().y() > 0 else -0.1
            old_zoom = self.zoom_level
            self.zoom_level = max(0.1, min(10.0, self.zoom_level + zoom_delta))

            # Adjust scroll offset to zoom around cursor position
            cursor_x = int(event.x() + self.scroll_offset)
            cursor_time = self.pixels_to_seconds(cursor_x)

            # Calculate new position after zoom
            new_cursor_x = self.seconds_to_pixels(cursor_time)
            self.scroll_offset = max(0, new_cursor_x - event.x())

            # Limit scrolling to timeline width
            max_offset = max(0, self.timeline_width() - self.width())
            self.scroll_offset = min(self.scroll_offset, max_offset)
        else:
            # Horizontal scrolling
            delta = event.angleDelta().y()
            scroll_delta = 100 if delta < 0 else -100
            new_offset = max(0, self.scroll_offset + scroll_delta)

            # Limit scrolling to timeline width
            max_offset = max(0, self.timeline_width() - self.width())
            self.scroll_offset = min(new_offset, max_offset)

        self.update()
        event.accept()

    def get_clip_at_position(self, pixel_x):
        """Get the index of the clip at the given pixel position"""
        if not self.clips:
            return -1

        pixels_per_second = self.pixels_per_second * self.zoom_level
        x = 0

        for i, clip in enumerate(self.clips):
            clip_duration = clip.get("duration", 0)
            if clip_duration <= 0:
                continue

            clip_width = int(clip_duration * pixels_per_second)
            if x <= pixel_x < x + clip_width:
                return i

            x += clip_width

        return -1

    def format_time(self, seconds):
        """Format time in seconds to mm:ss.ms"""
        minutes = int(seconds // 60)
        seconds_remainder = seconds % 60
        return f"{minutes}:{int(seconds_remainder):02d}.{int((seconds_remainder * 100) % 100):02d}"

    def paintEvent(self, event):
        """Draw the timeline"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        height = self.height()

        # Draw background
        painter.fillRect(0, 0, width, height, QColor(30, 30, 30))

        # Calculate pixels per second based on zoom
        pixels_per_second = self.pixels_per_second * self.zoom_level

        # Draw time markers and grid
        self.draw_time_markers(painter, width, height, pixels_per_second)

        # Draw clips
        if not self.clips or self.total_duration <= 0:
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(10, height // 2 + 5, "No clips available")
            return

        # Calculate music track height and main clips height
        music_height = 0
        if self.music_tracks:
            music_height = min(height * 0.25, 20 * len(self.music_tracks))

        main_clip_height = height - music_height - 5 if music_height > 0 else height

        # Draw clips with offset for scrolling
        x = -int(self.scroll_offset)
        for i, clip in enumerate(self.clips):
            clip_duration = clip.get("duration", 0)
            if clip_duration <= 0:
                continue

            # Calculate width based on duration
            clip_width = max(int(clip_duration * pixels_per_second), 2)

            # Skip clips outside the view
            if x + clip_width < 0 or x > width:
                x += clip_width
                continue

            # Determine if this clip is being hovered
            is_hover = i == self.hover_clip_index

            # Draw clip
            is_image = clip.get("is_image", False)
            has_changes = clip.get("has_pending_changes", False)
            clip_rect = QRect(x, 5, clip_width, int(main_clip_height - 10))

            # Clip background
            if is_image:
                base_color = QColor(60, 179, 113)  # Green for images
            else:
                base_color = QColor(65, 105, 225)  # Blue for videos

            # Adjust color for hover state
            if is_hover:
                base_color = base_color.lighter(130)

            painter.fillRect(clip_rect, base_color)

            # Draw pending changes indicator if needed
            if has_changes:
                indicator_rect = QRect(x + 5, 10, 10, 10)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 165, 0))  # Orange for pending changes
                painter.drawEllipse(indicator_rect)

            # Draw border
            border_color = QColor(200, 200, 200)
            painter.setPen(QPen(border_color, 1))
            painter.drawRect(clip_rect)

            # Draw clip number
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(x + 5, 20, f"{i+1}")

            # Draw clip name (truncated if needed)
            clip_name = clip.get("name", "")
            name_rect = QRect(
                x + 5, int(main_clip_height) // 2 - 10, clip_width - 10, 20
            )
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)

            if clip_width > 60:  # Only draw name if clip is wide enough
                name = painter.fontMetrics().elidedText(
                    clip_name, Qt.ElideMiddle, clip_width - 10
                )
                painter.drawText(name_rect, Qt.AlignCenter, name)

            # Draw duration
            duration_text = self.format_time(clip_duration)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(x + 5, int(main_clip_height - 10), duration_text)

            # Draw hover information if this clip is being hovered
            if is_hover and clip_width > 20:
                hover_pos = self.hover_x - x
                hover_time = self.pixels_to_seconds(hover_pos)
                if 0 <= hover_time <= clip_duration:
                    # Calculate the position within this clip
                    clip_start_time = clip.get("start_time", 0)
                    absolute_time = clip_start_time + hover_time

                    # Draw time indicator
                    painter.setPen(QPen(QColor(255, 165, 0), 2))  # Orange line
                    hover_x_pos = int(x + hover_pos)
                    painter.drawLine(
                        hover_x_pos, 5, hover_x_pos, int(main_clip_height - 5)
                    )

                    # Draw time label
                    time_label = f"{self.format_time(absolute_time)}"
                    painter.fillRect(
                        hover_x_pos - 40,
                        int(main_clip_height - 30),
                        80,
                        20,
                        QColor(0, 0, 0, 180),
                    )
                    painter.setPen(QColor(255, 165, 0))
                    painter.drawText(
                        hover_x_pos - 40,
                        int(main_clip_height - 30),
                        80,
                        20,
                        Qt.AlignCenter,
                        time_label,
                    )

            x += clip_width

        # Draw music tracks if any
        if self.music_tracks and music_height > 0:
            track_height = music_height / len(self.music_tracks)
            y_offset = main_clip_height + 5

            # Draw music track background
            painter.fillRect(
                0, int(y_offset), int(width), int(music_height), QColor(40, 40, 40)
            )

            # Draw track separator
            painter.setPen(QPen(QColor(60, 60, 60), 1))
            painter.drawLine(0, int(y_offset), int(width), int(y_offset))

            # Draw each track
            for i, track in enumerate(self.music_tracks):
                track_y = y_offset + i * track_height

                # Calculate start and end in pixels
                start_px = (
                    self.seconds_to_pixels(track.start_time_in_compilation)
                    - self.scroll_offset
                )
                duration = track.duration or (
                    track.total_duration - track.start_time_in_track
                )
                end_px = start_px + self.seconds_to_pixels(duration)

                # Draw track if visible
                if end_px >= 0 and start_px < width:
                    # Draw track background
                    track_rect = QRect(
                        int(max(0, start_px)),
                        int(track_y),
                        int(min(width, end_px) - max(0, start_px)),
                        int(track_height),
                    )

                    painter.fillRect(
                        track_rect, QColor(150, 100, 200, 180)
                    )  # Purple for music

                    # Draw track name if wide enough
                    if track_rect.width() > 60:
                        painter.setPen(QColor(255, 255, 255))
                        track_name = os.path.basename(track.file_path)
                        name = painter.fontMetrics().elidedText(
                            track_name, Qt.ElideMiddle, track_rect.width() - 10
                        )
                        painter.drawText(
                            int(track_rect.x() + 4),
                            int(track_rect.y() + track_height / 2 + 5),
                            name,
                        )

        # Draw current position marker
        if (
            self.total_duration > 0
            and 0 <= self.current_position <= self.total_duration
        ):
            pos_x = int(self.current_position * pixels_per_second) - int(
                self.scroll_offset
            )

            if 0 <= pos_x <= width:
                painter.setPen(QPen(QColor(255, 0, 0), 2))
                painter.drawLine(pos_x, 0, pos_x, height)

                # Draw position time
                position_text = self.format_time(self.current_position)

                # Position text background
                text_width = 80
                painter.fillRect(
                    pos_x - text_width // 2, 2, text_width, 20, QColor(0, 0, 0, 180)
                )

                painter.setPen(QColor(255, 0, 0))
                painter.drawText(
                    pos_x - text_width // 2,
                    2,
                    text_width,
                    20,
                    Qt.AlignCenter,
                    position_text,
                )

        # Draw pending changes indicator
        if self.has_pending_changes:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 0, 0, 180))
            painter.drawRect(width - 15, 5, 10, 10)

        # Draw zoom level indicator
        zoom_text = f"Zoom: {self.zoom_level:.1f}x"
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(width - 100, 20, zoom_text)

    def draw_time_markers(self, painter, width, height, pixels_per_second):
        """Draw time markers and grid lines"""
        # Determine appropriate interval for time markers based on zoom
        if pixels_per_second > 200:
            # Very zoomed in - show 1 second intervals
            interval = 1
        elif pixels_per_second > 100:
            # Moderately zoomed in - show 5 second intervals
            interval = 5
        elif pixels_per_second > 50:
            # Normal zoom - show 10 second intervals
            interval = 10
        elif pixels_per_second > 20:
            # Zoomed out - show 30 second intervals
            interval = 30
        else:
            # Very zoomed out - show minute intervals
            interval = 60

        # Calculate visible time range
        start_time = max(0, self.pixels_to_seconds(self.scroll_offset))
        end_time = self.pixels_to_seconds(self.scroll_offset + width)

        # Round start time down to nearest interval
        start_time = (start_time // interval) * interval

        # Draw markers
        painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.DotLine))

        for t in range(int(start_time), int(end_time) + interval, interval):
            x = int(t * pixels_per_second) - int(self.scroll_offset)

            # Draw vertical line
            painter.drawLine(x, 0, x, height)

            # Draw time label
            time_str = self.format_time(t)
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(x + 2, height - 2, time_str)
            painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.DotLine))


class VideoEffect:
    """Class representing a video effect to be applied to a media item"""

    def __init__(self, effect_type="none", parameters=None):
        self.effect_type = effect_type  # Type of effect (speed, filter, etc.)
        self.parameters = parameters or {}  # Parameters specific to the effect

    def get_ffmpeg_filter(self):
        """Get the ffmpeg filter string for this effect"""
        if self.effect_type == "none":
            return ""

        if self.effect_type == "speed":
            # Speed adjustment
            speed_factor = self.parameters.get("factor", 1.0)
            if speed_factor == 1.0:
                return ""

            # For speed adjustments, audio and video need different treatments
            if speed_factor > 1.0:
                # Faster playback (speed up)
                return f"setpts={1/speed_factor}*PTS,atempo={min(2.0, speed_factor)}"
            else:
                # Slower playback (slow down)
                return f"setpts={1/speed_factor}*PTS,atempo={max(0.5, speed_factor)}"

        elif self.effect_type == "filter":
            # Visual filter
            filter_name = self.parameters.get("name", "")

            if filter_name == "grayscale":
                return "hue=s=0"
            elif filter_name == "sepia":
                return (
                    "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"
                )
            elif filter_name == "vignette":
                return "vignette=PI/4"
            elif filter_name == "blur":
                blur_amount = self.parameters.get("amount", 5)
                return f"boxblur={blur_amount}:1"
            elif filter_name == "sharpen":
                return "unsharp=5:5:1.5:5:5:0.0"
            elif filter_name == "noise":
                return "noise=alls=20:allf=t"
            elif filter_name == "contrast":
                contrast = self.parameters.get("amount", 1.5)
                return f"eq=contrast={contrast}"
            elif filter_name == "brightness":
                brightness = self.parameters.get("amount", 0.1)
                return f"eq=brightness={brightness}"

        elif self.effect_type == "stabilize":
            # Video stabilization (simplified version)
            return "vidstabtransform=smoothing=10:input=/tmp/transforms.trf"

        # Default case - no filter
        return ""


class EffectsDialog(QDialog):
    """Dialog for editing video effects"""

    def __init__(self, parent=None, media_item=None):
        super().__init__(parent)
        self.media_item = media_item
        self.setWindowTitle("Video Effects")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        # Set up UI
        layout = QVBoxLayout(self)

        # Tabs for different effect categories
        self.tabs = QTabWidget()

        # Speed tab
        speed_tab = QWidget()
        speed_layout = QVBoxLayout(speed_tab)

        speed_label = QLabel("Playback Speed:")
        speed_layout.addWidget(speed_label)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(25, 400)  # 0.25x to 4.0x
        self.speed_slider.setValue(int(self.media_item.playback_speed * 100))
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.setTickInterval(25)

        self.speed_value_label = QLabel(f"{self.media_item.playback_speed:.2f}x")
        self.speed_slider.valueChanged.connect(self.update_speed_label)

        speed_layout.addWidget(self.speed_slider)
        speed_layout.addWidget(self.speed_value_label)

        # Speed presets
        presets_layout = QHBoxLayout()
        presets = [
            ("0.5x", 50),
            ("0.75x", 75),
            ("Normal (1.0x)", 100),
            ("1.5x", 150),
            ("2.0x", 200),
        ]

        for label, value in presets:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, v=value: self.speed_slider.setValue(v))
            presets_layout.addWidget(btn)

        speed_layout.addLayout(presets_layout)
        speed_layout.addStretch()

        self.tabs.addTab(speed_tab, "Speed")

        # Filters tab
        filters_tab = QWidget()
        filters_layout = QVBoxLayout(filters_tab)

        filters_label = QLabel("Visual Filters:")
        filters_layout.addWidget(filters_label)

        # Filter options
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("None", "none")
        self.filter_combo.addItem("Grayscale", "grayscale")
        self.filter_combo.addItem("Sepia", "sepia")
        self.filter_combo.addItem("Blur", "blur")
        self.filter_combo.addItem("Sharpen", "sharpen")
        self.filter_combo.addItem("Noise", "noise")
        self.filter_combo.addItem("Vignette", "vignette")
        self.filter_combo.addItem("Contrast", "contrast")
        self.filter_combo.addItem("Brightness", "brightness")

        filters_layout.addWidget(self.filter_combo)

        # Apply filter button
        apply_filter_btn = QPushButton("Apply Filter")
        apply_filter_btn.clicked.connect(self.apply_filter)
        filters_layout.addWidget(apply_filter_btn)

        # Current filters list
        filters_layout.addWidget(QLabel("Applied Filters:"))
        self.filters_list = QListWidget()
        self.filters_list.setMaximumHeight(120)
        filters_layout.addWidget(self.filters_list)

        # Remove filter button
        remove_filter_btn = QPushButton("Remove Selected Filter")
        remove_filter_btn.clicked.connect(self.remove_filter)
        filters_layout.addWidget(remove_filter_btn)

        # Populate current filters
        self.update_filters_list()

        self.tabs.addTab(filters_tab, "Filters")

        # Add tabs to layout
        layout.addWidget(self.tabs, 1)

        # Dialog buttons
        buttons = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(ok_button)

        layout.addLayout(buttons)

    def update_speed_label(self, value):
        """Update the speed label when the slider changes"""
        speed = value / 100.0
        self.speed_value_label.setText(f"{speed:.2f}x")

    def update_filters_list(self):
        """Update the list of applied filters"""
        self.filters_list.clear()
        for effect in self.media_item.effects:
            if effect.effect_type == "filter":
                filter_name = effect.parameters.get("name", "unknown")
                self.filters_list.addItem(filter_name.capitalize())

    def apply_filter(self):
        """Apply the selected filter"""
        filter_type = self.filter_combo.currentData()

        if filter_type == "none":
            return

        # Create filter parameters
        parameters = {"name": filter_type}

        # Create the effect
        effect = VideoEffect("filter", parameters)

        # Add the new effect
        self.media_item.effects.append(effect)

        # Mark item as having pending changes
        self.media_item.has_pending_changes = True

        # Update the list
        self.update_filters_list()

    def remove_filter(self):
        """Remove the selected filter"""
        selected_items = self.filters_list.selectedItems()
        if not selected_items:
            return

        filter_name = selected_items[0].text().lower()

        # Find and remove the filter
        for i, effect in enumerate(self.media_item.effects):
            if (
                effect.effect_type == "filter"
                and effect.parameters.get("name", "") == filter_name
            ):
                self.media_item.effects.pop(i)
                # Mark item as having pending changes
                self.media_item.has_pending_changes = True
                break

        # Update the list
        self.update_filters_list()

    def accept(self):
        """Apply changes and close dialog"""
        # Update the playback speed
        new_speed = self.speed_slider.value() / 100.0
        if new_speed != self.media_item.playback_speed:
            self.media_item.playback_speed = new_speed
            self.media_item.has_pending_changes = True

        # Invalid preview since effects changed
        if self.media_item.has_pending_changes:
            self.media_item.invalidate_preview()

        super().accept()


class MusicTrack:
    """Class representing a music track in the compilation"""

    def __init__(
        self,
        file_path,
        start_time_in_compilation=0.0,
        start_time_in_track=0.0,
        duration=None,
        volume=0.7,
    ):
        self.file_path = file_path
        self.start_time_in_compilation = (
            start_time_in_compilation  # When to start playing in the video
        )
        self.start_time_in_track = (
            start_time_in_track  # Where to start playing from the music file
        )
        self.duration = duration  # How long to play the music (None = play until the end or until the video ends)
        self.volume = volume  # Volume level (0.0 to 1.0)
        self.track_id = str(uuid.uuid4())[:8]  # Unique ID for this track

        # Get total duration of the music file
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                self.file_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed: {result.stderr}")

            probe = json.loads(result.stdout)
            self.total_duration = float(probe["format"]["duration"])

            # If duration is None, use the full track length
            if self.duration is None:
                self.duration = self.total_duration - self.start_time_in_track
        except Exception as e:
            print(f"Error getting music duration: {str(e)}")
            self.total_duration = 0
            self.duration = 0


class MusicEditorDialog(QDialog):
    """Dialog for managing multiple music tracks"""

    def __init__(self, parent=None, music_tracks=None, total_video_duration=0):
        super().__init__(parent)
        self.setWindowTitle("Music Editor")
        self.setMinimumWidth(800)
        self.setMinimumHeight(400)

        # Store data
        self.music_tracks = music_tracks.copy() if music_tracks else []
        self.total_video_duration = total_video_duration
        self.original_tracks = music_tracks.copy() if music_tracks else []
        self.changes_made = False

        # Initialize UI
        layout = QVBoxLayout(self)

        # Table for music tracks
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "File",
                "Start In Video",
                "Start In Track",
                "Duration",
                "Volume",
                "End Time",
                "Actions",
            ]
        )

        # Set column widths
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )

        layout.addWidget(self.table)

        # Timeline visualization
        timeline_label = QLabel("Music Timeline:")
        timeline_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(timeline_label)

        self.timeline_widget = QWidget()
        self.timeline_widget.setMinimumHeight(60)
        self.timeline_widget.setMaximumHeight(120)
        self.timeline_widget.paintEvent = self.paint_timeline
        layout.addWidget(self.timeline_widget)

        # Buttons
        button_layout = QHBoxLayout()

        add_button = QPushButton("Add Music Track")
        add_button.clicked.connect(self.add_track)

        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(add_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(self.ok_button)

        layout.addLayout(button_layout)

        # Populate table
        self.populate_table()

    def paint_timeline(self, event):
        """Paint the music timeline visualization"""
        if not self.music_tracks or self.total_video_duration <= 0:
            return

        painter = QPainter(self.timeline_widget)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.timeline_widget.width()
        height = self.timeline_widget.height()

        # Draw background
        painter.fillRect(0, 0, width, height, QColor(40, 40, 40))

        # Draw time markers
        painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.DotLine))
        interval = max(
            1, int(self.total_video_duration / 10)
        )  # Divide into ~10 segments

        for t in range(0, int(self.total_video_duration) + interval, interval):
            x = int((t / self.total_video_duration) * width)
            painter.drawLine(x, 0, x, height)

            # Draw time label
            minutes = t // 60
            seconds = t % 60
            time_str = f"{minutes}:{seconds:02d}"
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(x + 5, 15, time_str)
            painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.DotLine))

        # Draw tracks
        track_height = min(height / max(1, len(self.music_tracks)), 25)

        for i, track in enumerate(self.music_tracks):
            y = int(i * track_height + 20)  # Start below time markers, convert to int

            # Calculate track position
            start_x = int(
                (track.start_time_in_compilation / self.total_video_duration) * width
            )

            # Calculate duration - either specified or remainder of track
            if track.duration:
                duration = track.duration
            else:
                duration = track.total_duration - track.start_time_in_track

            # Limit by video length
            end_x = int(
                (
                    (track.start_time_in_compilation + duration)
                    / self.total_video_duration
                )
                * width
            )
            end_x = min(end_x, width)

            # Draw track block
            track_width = max(2, end_x - start_x)
            track_rect = QRect(start_x, y, track_width, int(track_height - 2))

            # Color based on volume
            color_intensity = int(155 + track.volume * 100)
            track_color = QColor(100, color_intensity, 200, 180)

            painter.fillRect(track_rect, track_color)

            # Draw border
            painter.setPen(QPen(QColor(200, 200, 200)))
            painter.drawRect(track_rect)

            # Draw track name if there's room
            if track_width > 60:
                track_name = os.path.basename(track.file_path)
                name = painter.fontMetrics().elidedText(
                    track_name, Qt.ElideMiddle, track_width - 10
                )
                # Fix: Convert float to int for y coordinate
                text_y = int(track_rect.y() + track_height / 2 + 5)
                painter.drawText(track_rect.x() + 5, text_y, name)

    def populate_table(self):
        """Populate the table with music tracks"""
        self.table.setRowCount(0)

        for i, track in enumerate(self.music_tracks):
            self.table.insertRow(i)

            # File name
            self.table.setItem(
                i, 0, QTableWidgetItem(os.path.basename(track.file_path))
            )

            # Start in video
            start_in_video = QDoubleSpinBox()
            start_in_video.setRange(0, max(self.total_video_duration, 3600))
            start_in_video.setValue(track.start_time_in_compilation)
            start_in_video.setSuffix(" sec")
            start_in_video.valueChanged.connect(
                lambda value, row=i: self.update_track(row, "start_comp", value)
            )
            self.table.setCellWidget(i, 1, start_in_video)

            # Start in track
            start_in_track = QDoubleSpinBox()
            start_in_track.setRange(0, track.total_duration)
            start_in_track.setValue(track.start_time_in_track)
            start_in_track.setSuffix(" sec")
            start_in_track.valueChanged.connect(
                lambda value, row=i: self.update_track(row, "start_track", value)
            )
            self.table.setCellWidget(i, 2, start_in_track)

            # Duration
            duration_spin = QDoubleSpinBox()
            duration_spin.setRange(0.1, 3600)
            if track.duration:
                duration_spin.setValue(track.duration)
            else:
                duration_spin.setValue(track.total_duration - track.start_time_in_track)
            duration_spin.setSuffix(" sec")
            duration_spin.valueChanged.connect(
                lambda value, row=i: self.update_track(row, "duration", value)
            )
            self.table.setCellWidget(i, 3, duration_spin)

            # Volume
            volume_spin = QDoubleSpinBox()
            volume_spin.setRange(0, 1)
            volume_spin.setSingleStep(0.1)
            volume_spin.setDecimals(1)
            volume_spin.setValue(track.volume)
            volume_spin.valueChanged.connect(
                lambda value, row=i: self.update_track(row, "volume", value)
            )
            self.table.setCellWidget(i, 4, volume_spin)

            # End time (calculated)
            end_time = track.start_time_in_compilation + (
                track.duration or (track.total_duration - track.start_time_in_track)
            )
            end_time_item = QTableWidgetItem(f"{end_time:.2f} sec")
            end_time_item.setFlags(end_time_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 5, end_time_item)

            # Actions button
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(0, 0, 0, 0)

            delete_btn = QPushButton("Delete")
            delete_btn.setStyleSheet("background-color: #e74c3c; color: white;")
            delete_btn.clicked.connect(lambda _, row=i: self.delete_track(row))

            actions_layout.addWidget(delete_btn)

            actions_widget = QWidget()
            actions_widget.setLayout(actions_layout)
            self.table.setCellWidget(i, 6, actions_widget)

    def update_track(self, row, property_name, value):
        """Update a track property and refresh calculations"""
        if 0 <= row < len(self.music_tracks):
            track = self.music_tracks[row]
            self.changes_made = True

            if property_name == "start_comp":
                track.start_time_in_compilation = value
            elif property_name == "start_track":
                track.start_time_in_track = value
                # Update max possible duration
                max_duration = track.total_duration - track.start_time_in_track
                duration_widget = self.table.cellWidget(row, 3)
                if duration_widget and isinstance(duration_widget, QDoubleSpinBox):
                    if duration_widget.value() > max_duration:
                        duration_widget.setValue(max_duration)
                    duration_widget.setMaximum(max_duration)
            elif property_name == "duration":
                track.duration = value
            elif property_name == "volume":
                track.volume = value

            # Update end time
            end_time = track.start_time_in_compilation + (
                track.duration or (track.total_duration - track.start_time_in_track)
            )
            self.table.item(row, 5).setText(f"{end_time:.2f} sec")

            # Update timeline
            self.timeline_widget.update()

    def add_track(self):
        """Add a new music track"""
        music_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Music File",
            "",
            "Audio Files (*.mp3 *.wav *.ogg *.aac *.m4a *.flac)",
        )

        if not music_path:
            return

        # Get the estimated total duration of the video
        # If there are existing tracks, suggest starting after the last one
        start_time = 0
        if self.music_tracks:
            last_track = self.music_tracks[-1]
            last_end = last_track.start_time_in_compilation + (
                last_track.duration
                or (last_track.total_duration - last_track.start_time_in_track)
            )
            start_time = max(0, last_end)

        # Create a new track
        new_track = MusicTrack(music_path, start_time_in_compilation=start_time)
        self.music_tracks.append(new_track)
        self.changes_made = True

        # Refresh the table
        self.populate_table()

        # Update timeline
        self.timeline_widget.update()

    def delete_track(self, row):
        """Delete a music track"""
        if 0 <= row < len(self.music_tracks):
            del self.music_tracks[row]
            self.changes_made = True
            self.populate_table()

            # Update timeline
            self.timeline_widget.update()

    def accept(self):
        """Apply changes and return"""
        if self.changes_made:
            # Mark that there are pending changes that need to be re-rendered
            result = (
                QMessageBox.information(
                    self,
                    "Music Changes",
                    "Music changes will be applied when you preview or export the video.",
                    QMessageBox.Ok,
                ),
            )
        super().accept()

    def reject(self):
        """Cancel and discard changes"""
        if self.changes_made:
            result = (
                QMessageBox.question(
                    self,
                    "Discard Changes",
                    "You have made changes to the music tracks. Discard these changes?",
                    QMessageBox.Yes | QMessageBox.No,
                ),
            )
            if result == QMessageBox.Yes:
                # Restore original tracks
                self.music_tracks.clear()
                self.music_tracks.extend(self.original_tracks)
                super().reject()
            # Otherwise do nothing - stay in the dialog
        else:
            super().reject()


class VideoCompilationEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        # Clean up temp files on startup
        cleanup_temp_dirs()

        # Set window properties
        self.setWindowTitle("Historian Video Editor")
        self.setGeometry(100, 100, 1200, 720)
        self.setMinimumSize(900, 600)
        self.setWindowIcon(QIcon("historian_icon.png"))  # Ensure icon exists

        # Media player setup
        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.video_widget = QVideoWidget()
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.stateChanged.connect(self.media_state_changed)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.error.connect(self.handle_player_error)

        # State tracking
        self.preview_file = None
        self.current_item = None
        self.default_image_duration = 5.0
        self.position_slider_being_dragged = False
        self.progress_dialog = None
        self.is_processing = False
        self.thread_active = False  # Track thread status
        self.preview_all_cache = {"signature": None, "path": None, "total_duration": 0}
        self.music_file = None
        self.music_volume = 0.7
        self.music_tracks = []
        self.has_pending_music_changes = False

        # Critical UI elements initialized here for persistence
        self.preview_all_btn = QPushButton("Preview All")
        self.preview_all_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.preview_all_btn.setMinimumSize(140, 48)
        self.preview_all_btn.clicked.connect(self.preview_all)

        self.export_btn = QPushButton("Export")
        self.export_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.export_btn.setMinimumSize(140, 48)
        self.export_btn.clicked.connect(self.export)

        # Processing thread
        self.processing_thread = None  # Initialized on demand

        # UI setup
        self.setup_ui()

    def setup_ui(self):
        """Set up the user interface with improved structure."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        add_videos_btn = QPushButton(
            "Add Videos", icon=self.style().standardIcon(QStyle.SP_FileDialogStart)
        )
        add_videos_btn.clicked.connect(self.add_videos)
        add_videos_btn.setMinimumSize(120, 40)
        add_videos_btn.setToolTip("Add video files to the compilation")

        add_images_btn = QPushButton(
            "Add Images", icon=self.style().standardIcon(QStyle.SP_DirIcon)
        )
        add_images_btn.clicked.connect(self.add_images)
        add_images_btn.setMinimumSize(120, 40)
        add_images_btn.setToolTip("Add image files to the compilation")

        edit_btn = QPushButton(
            "Edit", icon=self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        )
        edit_btn.clicked.connect(self.edit_selected)
        edit_btn.setMinimumSize(100, 32)
        edit_btn.setToolTip("Edit selected media properties")

        delete_btn = QPushButton(
            "Remove", icon=self.style().standardIcon(QStyle.SP_TrashIcon)
        )
        delete_btn.clicked.connect(self.delete_selected)
        delete_btn.setMinimumSize(100, 32)
        delete_btn.setToolTip("Remove selected media item")

        randomize_btn = QPushButton(
            "Shuffle", icon=self.style().standardIcon(QStyle.SP_BrowserReload)
        )
        randomize_btn.clicked.connect(self.randomize_order)
        randomize_btn.setMinimumSize(100, 32)
        randomize_btn.setToolTip("Randomize order of media items")

        add_music_btn = QPushButton(
            "Add Music", icon=self.style().standardIcon(QStyle.SP_MediaVolume)
        )
        add_music_btn.clicked.connect(self.add_music)
        add_music_btn.setMinimumSize(120, 40)
        add_music_btn.setToolTip("Add background music tracks")

        toolbar.addWidget(add_videos_btn)
        toolbar.addWidget(add_images_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(delete_btn)
        toolbar.addWidget(randomize_btn)
        toolbar.addStretch()
        toolbar.addWidget(add_music_btn)
        toolbar.addWidget(self.preview_all_btn)
        toolbar.addWidget(self.export_btn)
        main_layout.addLayout(toolbar)

        # Apply stylesheet (unchanged from original)
        self.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #F8F1E9; }
            QLabel { color: #2E2E2E; font-family: 'Times New Roman', serif; font-size: 12px; }
            QPushButton { background-color: #2E2E2E; color: #F8F1E9; border: 2px solid #D4A017; padding: 6px 12px; border-radius: 6px; font-family: 'Times New Roman', serif; font-size: 14px; font-weight: bold; }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3A3A3A, stop:1 #2E2E2E); color: #D4A017; }
            QPushButton:pressed { background-color: #D4A017; color: #2E2E2E; border: 2px solid #5C4033; padding: 8px 14px; }
            QPushButton:disabled { background-color: #A0A0A0; color: #D9D9D9; border: 2px solid #A0A0A0; }
            QListWidget { background-color: #FFFFFF; border: 1px solid #D4A017; border-radius: 4px; padding: 5px; font-family: 'Roboto', sans-serif; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #E8E8E8; color: #2E2E2E; }
            QListWidget::item:selected { background-color: #D4A017; color: #F8F1E9; }
            QToolButton { background-color: #2E2E2E; color: #F8F1E9; border: 1px solid #D4A017; border-radius: 4px; }
            QToolButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3A3A3A, stop:1 #2E2E2E); color: #D4A017; }
            QFrame#statusFrame { background-color: #F0E9E0; border: 1px solid #D4A017; border-radius: 4px; padding: 5px; }
            QSlider::groove:horizontal { height: 6px; background: #E8E8E8; border-radius: 3px; }
            QSlider::handle:horizontal { background: #D4A017; border: 1px solid #2E2E2E; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #5C4033; border-radius: 3px; }
            QProgressDialog { background-color: #FFFFFF; border: 1px solid #D4A017; }
            QProgressDialog QLabel { font-size: 14px; padding: 8px; color: #2E2E2E; font-family: 'Times New Roman', serif; }
            QProgressDialog QPushButton { background-color: #5C4033; color: #F8F1E9; border: 1px solid #D4A017; }
            QProgressDialog QPushButton:hover { background-color: #7A5845; }
        """
        )

        # Main content splitter
        splitter = QSplitter(Qt.Horizontal)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        list_label = QLabel("Media Items")
        list_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(list_label)

        self.clip_list = QListWidget()
        self.clip_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.clip_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.clip_list.itemSelectionChanged.connect(self.selection_changed)
        self.clip_list.itemDoubleClicked.connect(self.edit_selected)
        self.clip_list.model().rowsMoved.connect(self.on_items_reordered)
        left_layout.addWidget(self.clip_list)

        preview_btn = QPushButton(
            "Preview Selected", icon=self.style().standardIcon(QStyle.SP_MediaPlay)
        )
        preview_btn.clicked.connect(self.preview_selected_item)
        preview_btn.setToolTip("Preview the selected media item")
        left_layout.addWidget(preview_btn)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        preview_label = QLabel("Preview")
        preview_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(preview_label)

        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout.addWidget(self.video_widget)

        playback_layout = QHBoxLayout()
        self.play_button = QToolButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setIconSize(QSize(24, 24))
        self.play_button.setFixedSize(36, 36)
        self.play_button.clicked.connect(self.play_pause)
        self.play_button.setToolTip("Play/Pause video")

        self.stop_button = QToolButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setIconSize(QSize(24, 24))
        self.stop_button.setFixedSize(36, 36)
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setToolTip("Stop video playback")

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self.slider_pressed)
        self.position_slider.sliderReleased.connect(self.slider_released)
        self.position_slider.setToolTip("Seek through the video")

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setMinimumWidth(80)

        playback_layout.addWidget(self.play_button)
        playback_layout.addWidget(self.stop_button)
        playback_layout.addWidget(self.position_slider)
        playback_layout.addWidget(self.time_label)
        right_layout.addLayout(playback_layout)

        timeline_header = QHBoxLayout()
        timeline_label = QLabel("Timeline:")
        timeline_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        timeline_help = QLabel("(Drag to scroll, Ctrl+Wheel to zoom)")
        timeline_help.setStyleSheet("font-size: 10px; color: #888;")
        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedWidth(30)
        zoom_out_btn.clicked.connect(self.zoom_out_timeline)
        zoom_out_btn.setToolTip("Zoom out timeline")
        zoom_fit_btn = QPushButton("Fit")
        zoom_fit_btn.setFixedWidth(40)
        zoom_fit_btn.clicked.connect(self.zoom_fit_timeline)
        zoom_fit_btn.setToolTip("Fit timeline to view")
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(30)
        zoom_in_btn.clicked.connect(self.zoom_in_timeline)
        zoom_in_btn.setToolTip("Zoom in timeline")

        timeline_header.addWidget(timeline_label)
        timeline_header.addWidget(timeline_help)
        timeline_header.addStretch()
        timeline_header.addWidget(zoom_out_btn)
        timeline_header.addWidget(zoom_fit_btn)
        timeline_header.addWidget(zoom_in_btn)
        right_layout.addLayout(timeline_header)

        self.timeline = TimelineWidget()
        right_layout.addWidget(self.timeline)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])
        main_layout.addWidget(splitter, 1)

        status_frame = QFrame()
        status_frame.setObjectName("statusFrame")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 5, 10, 5)

        self.status_label = QLabel("Historian: Ready")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.status_label)

        self.changes_indicator = QLabel("")
        self.changes_indicator.setStyleSheet("color: #7A5845;")
        status_layout.addWidget(self.changes_indicator, 0, Qt.AlignRight)
        main_layout.addWidget(status_frame)

    def set_position(self, position):
        """Set media position with safe seeking."""
        if self.media_player.isSeekable():
            was_playing = self.media_player.state() == QMediaPlayer.PlayingState
            if was_playing:
                self.media_player.pause()
            self.media_player.setPosition(position)
            if was_playing:
                QTimer.singleShot(100, self.media_player.play)
        else:
            print("Media is not seekable")

    def processing_finished(self, task, result):
        """Handle processing thread completion with robust cleanup."""
        self.is_processing = False
        self.thread_active = False
        if self.progress_dialog:
            try:
                self.progress_dialog.close()
            except Exception as e:
                print(f"Error closing progress dialog: {e}")
            finally:
                self.progress_dialog = None
        if task == "preview_all" and self.preview_all_btn:
            try:
                self.preview_all_btn.setText("Preview All")
                self.preview_all_btn.clicked.disconnect()
                self.preview_all_btn.clicked.connect(self.preview_all)
            except Exception as e:
                print(f"Error resetting preview_all_btn: {e}")
        elif task == "export" and self.export_btn:
            try:
                self.export_btn.setText("Export")
                self.export_btn.clicked.disconnect()
                self.export_btn.clicked.connect(self.export)
            except Exception as e:
                print(f"Error resetting export_btn: {e}")
        if result == "Aborted":
            self.status_label.setText("Historian: Operation canceled")
        elif isinstance(result, str) and result.startswith("Error"):
            self.status_label.setText("Historian: Operation failed")
            QMessageBox.warning(self, "Error", result)
        else:
            self.status_label.setText(f"Historian: {task.capitalize()} completed")
            if task == "preview_all" and isinstance(result, tuple):
                self.preview_file = result[0]
                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(result[0])))
                self.media_player.play()

    def zoom_in_timeline(self):
        if self.timeline:
            self.timeline.zoom_level = min(10.0, self.timeline.zoom_level * 1.25)
            self.timeline.update()

    def zoom_out_timeline(self):
        if self.timeline:
            self.timeline.zoom_level = max(0.1, self.timeline.zoom_level / 1.25)
            self.timeline.update()

    def zoom_fit_timeline(self):
        if self.timeline:
            self.timeline.zoom_level = 1.0
            self.timeline.scroll_offset = 0
            self.timeline.update()

    def on_items_reordered(self):
        self.update_timeline()
        self.preview_all_cache["signature"] = None

    def cancel_processing(self):
        """Cancel processing with robust thread and UI cleanup."""
        if (
            self.thread_active
            and self.processing_thread
            and self.processing_thread.isRunning()
        ):
            self.processing_thread.abort()
            self.processing_thread.wait()
        self.is_processing = False
        self.thread_active = False
        if self.progress_dialog:
            try:
                self.progress_dialog.close()
            except Exception as e:
                print(f"Error closing progress dialog: {e}")
            finally:
                self.progress_dialog = None
        self.status_label.setText("Historian: Processing canceled")
        if self.preview_all_btn:
            try:
                self.preview_all_btn.setText("Preview All")
                self.preview_all_btn.clicked.disconnect()
                self.preview_all_btn.clicked.connect(self.preview_all)
            except Exception as e:
                print(f"Error resetting preview_all_btn: {e}")
        if self.export_btn:
            try:
                self.export_btn.setText("Export")
                self.export_btn.clicked.disconnect()
                self.export_btn.clicked.connect(self.export)
            except Exception as e:
                print(f"Error resetting export_btn: {e}")

    def media_state_changed(self, state):
        if self.play_button:
            if state == QMediaPlayer.PlayingState:
                self.play_button.setIcon(
                    self.style().standardIcon(QStyle.SP_MediaPause)
                )
            else:
                self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def play_pause(self):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def stop(self):
        self.media_player.stop()

    def position_changed(self, position):
        if not self.position_slider_being_dragged and self.position_slider:
            self.position_slider.setValue(position)
        duration = self.media_player.duration()
        if duration > 0 and self.time_label:
            position_sec = position / 1000.0
            duration_sec = duration / 1000.0
            self.time_label.setText(
                f"{int(position_sec // 60)}:{int(position_sec % 60):02d} / {int(duration_sec // 60)}:{int(duration_sec % 60):02d}"
            )
            if self.timeline:
                self.timeline.set_position(position_sec)

    def duration_changed(self, duration):
        if self.position_slider and duration > 0:
            self.position_slider.setRange(0, duration)
            duration_sec = duration / 1000.0
            self.time_label.setText(
                f"0:00 / {int(duration_sec // 60)}:{int(duration_sec % 60):02d}"
            )

    def slider_pressed(self):
        self.position_slider_being_dragged = True
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()

    def slider_released(self):
        self.position_slider_being_dragged = False
        if self.position_slider:
            self.set_position(self.position_slider.value())

    def handle_player_error(self, error):
        if error != QMediaPlayer.NoError:
            self.status_label.setText(f"Media player error: {error}")
            print(f"Media player error: {error}")

    def update_progress(self, value, message):
        if self.progress_dialog:
            try:
                self.progress_dialog.setValue(value)
                self.progress_dialog.setLabelText(message)
            except Exception as e:
                print(f"Progress update error: {e}")

    def check_pending_changes(self):
        has_pending_changes = False
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            media_item = item.data(Qt.UserRole)
            if media_item.has_pending_changes:
                has_pending_changes = True
                break
        if self.has_pending_music_changes:
            has_pending_changes = True
        if has_pending_changes:
            self.changes_indicator.setText("⚠️ Pending changes - preview to see updates")
            if self.timeline:
                self.timeline.set_pending_changes(True)
        else:
            self.changes_indicator.setText("")
            if self.timeline:
                self.timeline.set_pending_changes(False)
        return has_pending_changes

    def processing_error(self, task, error_msg):
        self.is_processing = False
        self.thread_active = False
        if self.progress_dialog:
            try:
                self.progress_dialog.close()
            except:
                pass
            finally:
                self.progress_dialog = None
        self.status_label.setText(f"Error during {task}: {error_msg}")
        QMessageBox.warning(self, "Error", f"An error occurred: {error_msg}")

    def play_with_external_player(self, video_file):
        try:
            if platform.system() == "Windows":
                os.startfile(video_file)
            elif platform.system() == "Darwin":
                subprocess.call(("open", video_file))
            else:
                if shutil.which("vlc"):
                    subprocess.Popen(["vlc", video_file])
                elif shutil.which("mpv"):
                    subprocess.Popen(["mpv", video_file])
                else:
                    subprocess.call(("xdg-open", video_file))
            return True
        except Exception as e:
            print(f"Error launching external player: {e}")
            return False

    def closeEvent(self, event):
        self.media_player.stop()
        if (
            self.thread_active
            and self.processing_thread
            and self.processing_thread.isRunning()
        ):
            self.processing_thread.abort()
            self.processing_thread.wait()
        cleanup_temp_dirs()
        event.accept()

    def add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Videos",
            "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.m4v *.webm)",
        )
        if not files:
            return
        progress = (
            QProgressDialog("Importing videos...", "Cancel", 0, len(files), self)
            if len(files) > 3
            else None
        )
        if progress:
            progress.setWindowTitle("Importing")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
        failed_files = []
        for i, file_path in enumerate(files):
            if progress:
                progress.setValue(i)
                progress.setLabelText(f"Importing {os.path.basename(file_path)}...")
                if progress.wasCanceled():
                    break
            try:
                clip = VideoClip(file_path)
                item = QListWidgetItem(os.path.basename(file_path))
                item.setData(Qt.UserRole, clip)
                self.clip_list.addItem(item)
            except ValueError as e:
                failed_files.append(f"{os.path.basename(file_path)}: {str(e)}")
            QApplication.processEvents()
        if progress:
            progress.setValue(len(files))
        if failed_files:
            error_msg = (
                "Failed to import:\n"
                + "\n".join(failed_files[:5])
                + (
                    f"\n...and {len(failed_files) - 5} more"
                    if len(failed_files) > 5
                    else ""
                )
            )
            QMessageBox.warning(self, "Import Errors", error_msg)
        if len(files) > len(failed_files):
            self.status_label.setText(
                f"Historian: Added {len(files) - len(failed_files)} videos"
            )
            self.clip_list.setCurrentRow(0)
        self.update_timeline()
        self.preview_all_cache["signature"] = None

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            "",
            "Image Files (*.jpg *.jpeg *.png *.bmp *.gif *.webp)",
        )
        if not files:
            return
        dialog = ImageDurationDialog(self, self.default_image_duration)
        if not dialog.exec_():
            return
        duration = dialog.duration_spin.value()
        apply_to_all = dialog.apply_to_all.isChecked()
        self.default_image_duration = duration
        progress = (
            QProgressDialog("Importing images...", "Cancel", 0, len(files), self)
            if len(files) > 3
            else None
        )
        if progress:
            progress.setWindowTitle("Importing")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
        failed_files = []
        for i, file_path in enumerate(files):
            if progress:
                progress.setValue(i)
                progress.setLabelText(f"Importing {os.path.basename(file_path)}...")
                if progress.wasCanceled():
                    break
            try:
                image = ImageItem(file_path)
                if apply_to_all:
                    image.display_duration = duration
                    image.duration = duration
                    image.end_time = duration
                item = QListWidgetItem(os.path.basename(file_path))
                item.setData(Qt.UserRole, image)
                self.clip_list.addItem(item)
            except ValueError as e:
                failed_files.append(f"{os.path.basename(file_path)}: {str(e)}")
            QApplication.processEvents()
        if progress:
            progress.setValue(len(files))
        if failed_files:
            error_msg = (
                "Failed to import:\n"
                + "\n".join(failed_files[:5])
                + (
                    f"\n...and {len(failed_files) - 5} more"
                    if len(failed_files) > 5
                    else ""
                )
            )
            QMessageBox.warning(self, "Import Errors", error_msg)
        if len(files) > len(failed_files):
            self.status_label.setText(f"Added {len(files) - len(failed_files)} images")
            self.clip_list.setCurrentRow(0)
        self.update_timeline()
        self.preview_all_cache["signature"] = None

    def edit_selected(self):
        if not self.current_item:
            QMessageBox.information(
                self, "No Selection", "Please select a media item to edit."
            )
            return
        dialog = EditDialog(self, self.current_item)
        if dialog.exec_():
            self.current_item.invalidate_preview()
            self.status_label.setText(
                f"Updated {os.path.basename(self.current_item.file_path)}"
            )
            self.update_timeline()
            self.check_pending_changes()
            self.preview_all_cache["signature"] = None

    def randomize_order(self):
        if self.clip_list.count() <= 1:
            return
        items = [
            (self.clip_list.item(i).text(), self.clip_list.item(i).data(Qt.UserRole))
            for i in range(self.clip_list.count())
        ]
        random.shuffle(items)
        self.clip_list.clear()
        for text, data in items:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, data)
            self.clip_list.addItem(item)
        self.status_label.setText("Historian: Items shuffled")
        self.update_timeline()
        self.preview_all_cache["signature"] = None
        toast = QMessageBox(self)
        toast.setWindowTitle("Historian")
        toast.setText("Clips randomized successfully.")
        toast.setStandardButtons(QMessageBox.NoButton)
        toast.show()
        QTimer.singleShot(1500, toast.close)

    def delete_selected(self):
        if not self.current_item:
            QMessageBox.information(
                self, "No Selection", "Please select a media item to remove."
            )
            return
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            if item.data(Qt.UserRole) == self.current_item:
                if self.current_item.preview_file and os.path.exists(
                    self.current_item.preview_file
                ):
                    try:
                        os.unlink(self.current_item.preview_file)
                    except Exception as e:
                        print(f"Error deleting preview file: {e}")
                self.clip_list.takeItem(i)
                self.current_item = None
                self.status_label.setText("Item removed")
                self.update_timeline()
                self.preview_all_cache["signature"] = None
                break

    def selection_changed(self):
        selected_items = self.clip_list.selectedItems()
        if selected_items:
            self.current_item = selected_items[0].data(Qt.UserRole)
            file_name = os.path.basename(self.current_item.file_path)
            self.status_label.setText(
                f"Selected: {file_name} (Preview available)"
                if self.current_item.preview_status == "ready"
                else f"Selected: {file_name} (Use Preview button to preview)"
            )
            if self.timeline:
                index = self.clip_list.currentRow()
                if index >= 0:
                    self.timeline.hover_clip_index = index
                    self.timeline.update()
        else:
            self.current_item = None
            self.status_label.setText("Ready")
            if self.timeline:
                self.timeline.hover_clip_index = -1
                self.timeline.update()

    def preview_selected_item(self):
        if not self.current_item:
            QMessageBox.information(
                self, "No Selection", "Please select a media item to preview."
            )
            return
        if self.is_processing:
            QMessageBox.information(
                self, "Processing", "Please wait for the current operation to complete."
            )
            return
        if (
            not self.current_item.has_pending_changes
            and self.current_item.preview_status == "ready"
            and self.current_item.preview_file
            and os.path.exists(self.current_item.preview_file)
        ):
            self.preview_file = self.current_item.preview_file
            self.media_player.setMedia(
                QMediaContent(QUrl.fromLocalFile(self.preview_file))
            )
            self.media_player.play()
            self.status_label.setText(
                f"Historian: Playing: {os.path.basename(self.current_item.file_path)}"
            )
            return
        self.is_processing = True
        self.thread_active = True
        self.current_item.preview_status = "generating"
        self.status_label.setText(
            f"Historian: Creating preview for {os.path.basename(self.current_item.file_path)}..."
        )
        self.progress_dialog = QProgressDialog(
            "Creating preview...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setWindowTitle("Preview")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)
        self.progress_dialog.setValue(0)
        self.processing_thread = ProcessingThread(self)
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.finished.connect(self.processing_finished)
        self.processing_thread.error.connect(self.processing_error)
        self.processing_thread.setup_task("preview_item", [self.current_item])
        self.processing_thread.start()

    def preview_all(self):
        if self.clip_list.count() == 0:
            QMessageBox.information(
                self, "No Items", "Please add some media items first."
            )
            return
        if self.is_processing:
            QMessageBox.information(
                self, "Processing", "Please wait for the current operation to complete."
            )
            return
        items = [
            self.clip_list.item(i).data(Qt.UserRole)
            for i in range(self.clip_list.count())
        ]
        need_new_preview = self.check_pending_changes()
        if (
            not need_new_preview
            and self.preview_all_cache["path"]
            and os.path.exists(self.preview_all_cache["path"])
        ):
            self.preview_file = self.preview_all_cache["path"]
            self.status_label.setText("Historian: Playing: All items (cached)")
            self.media_player.setMedia(
                QMediaContent(QUrl.fromLocalFile(self.preview_file))
            )
            self.media_player.play()
            return
        self.update_timeline()
        self.is_processing = True
        self.thread_active = True
        self.status_label.setText("Historian: Creating full preview...")
        if self.preview_all_btn:
            try:
                self.preview_all_btn.setText("Cancel Preview")
                self.preview_all_btn.clicked.disconnect()
                self.preview_all_btn.clicked.connect(self.cancel_processing)
            except Exception as e:
                print(f"Error updating preview_all_btn: {e}")
        self.progress_dialog = QProgressDialog(
            "Creating preview...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setWindowTitle("Preview")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)
        self.progress_dialog.setValue(0)
        self.processing_thread = ProcessingThread(self)
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.finished.connect(self.processing_finished)
        self.processing_thread.error.connect(self.processing_error)
        self.processing_thread.worker.music_tracks = self.music_tracks
        if self.music_tracks and len(self.music_tracks) > 0:
            self.processing_thread.worker.music_file = self.music_tracks[0].file_path
            self.processing_thread.worker.music_volume = self.music_tracks[0].volume
        else:
            self.processing_thread.worker.music_file = None
        self.processing_thread.setup_task("preview_all", [items])
        self.processing_thread.start()

    def export(self):
        if self.clip_list.count() == 0:
            QMessageBox.information(
                self, "No Items", "Please add some media items first."
            )
            return
        if self.is_processing:
            QMessageBox.information(
                self, "Processing", "Please wait for the current operation to complete."
            )
            return
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save Compilation", "", "Video Files (*.mp4)"
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".mp4"):
            output_path += ".mp4"
        items = [
            self.clip_list.item(i).data(Qt.UserRole)
            for i in range(self.clip_list.count())
        ]
        self.is_processing = True
        self.thread_active = True
        self.status_label.setText("Exporting...")
        self.progress_dialog = QProgressDialog(
            "Exporting video...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setWindowTitle("Export")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)
        self.progress_dialog.setValue(0)
        self.processing_thread = ProcessingThread(self)
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.finished.connect(self.processing_finished)
        self.processing_thread.error.connect(self.processing_error)
        self.processing_thread.worker.music_tracks = self.music_tracks
        if self.music_tracks and len(self.music_tracks) > 0:
            self.processing_thread.worker.music_file = self.music_tracks[0].file_path
            self.processing_thread.worker.music_volume = self.music_tracks[0].volume
        else:
            self.processing_thread.worker.music_file = None
        self.processing_thread.setup_task("export", [items, output_path])
        self.processing_thread.start()

    def add_music(self):
        total_duration = sum(
            (
                item.data(Qt.UserRole).display_duration
                if item.data(Qt.UserRole).is_image
                else (
                    item.data(Qt.UserRole).end_time or item.data(Qt.UserRole).duration
                )
                - item.data(Qt.UserRole).start_time
            )
            for i in range(self.clip_list.count())
            for item in [self.clip_list.item(i)]
        )
        if self.music_file and not self.music_tracks:
            try:
                track = MusicTrack(self.music_file, volume=self.music_volume)
                self.music_tracks.append(track)
            except Exception as e:
                print(f"Error converting music file to track: {e}")
        dialog = MusicEditorDialog(self, self.music_tracks, total_duration)
        if dialog.exec_():
            old_tracks = self.music_tracks.copy()
            self.music_tracks = dialog.music_tracks
            tracks_changed = len(old_tracks) != len(self.music_tracks) or any(
                track.file_path != old_tracks[i].file_path
                or track.start_time_in_compilation
                != old_tracks[i].start_time_in_compilation
                or track.start_time_in_track != old_tracks[i].start_time_in_track
                or track.duration != old_tracks[i].duration
                or track.volume != old_tracks[i].volume
                for i, track in enumerate(self.music_tracks)
                if i < len(old_tracks)
            )
            if self.music_tracks:
                self.music_file = self.music_tracks[0].file_path
                self.music_volume = self.music_tracks[0].volume
            else:
                self.music_file = None
            if tracks_changed:
                self.preview_all_cache = {
                    "signature": None,
                    "path": None,
                    "total_duration": 0,
                }
                self.has_pending_music_changes = True
                self.check_pending_changes()
            self.status_label.setText(
                "No music tracks"
                if not self.music_tracks
                else (
                    f"1 music track added: {os.path.basename(self.music_tracks[0].file_path)}"
                    if len(self.music_tracks) == 1
                    else f"{len(self.music_tracks)} music tracks added"
                )
            )
            self.timeline.set_music_tracks(self.music_tracks)
            self.timeline.update()

    def update_timeline(self):
        if self.clip_list.count() == 0:
            self.timeline.set_clips([])
            return
        timeline_clips = []
        current_time = 0
        for i in range(self.clip_list.count()):
            media_item = self.clip_list.item(i).data(Qt.UserRole)
            duration = (
                media_item.display_duration
                if media_item.is_image
                else (media_item.end_time or media_item.duration)
                - media_item.start_time
            )
            timeline_clips.append(
                {
                    "name": os.path.basename(media_item.file_path),
                    "duration": duration,
                    "start_time": current_time,
                    "is_image": media_item.is_image,
                    "has_pending_changes": media_item.has_pending_changes,
                }
            )
            current_time += duration
        self.timeline.set_clips(timeline_clips)
        self.timeline.set_music_tracks(self.music_tracks)
        self.timeline.update()


# Add the main execution point at the end of the file
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoCompilationEditor()
    window.show()
    sys.exit(app.exec_())
    (app.exec_())
