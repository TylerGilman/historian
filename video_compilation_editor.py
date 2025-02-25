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
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QListWidget, QListWidgetItem, QSlider, QLabel, QFileDialog, QMessageBox, 
                             QAbstractItemView, QProgressDialog, QDialog, QSpinBox, QCheckBox, QSplitter,
                             QToolButton, QSizePolicy, QStyle, QFrame, QTabWidget)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QMediaPlaylist
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import Qt, QUrl, QThread, pyqtSignal, QSize, QObject, QMetaObject, Q_ARG
from PyQt5.QtGui import QIcon, QFont, QPalette, QColor

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
            ['ffmpeg', '-hide_banner', '-encoders'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if 'h264_nvenc' in nvidia.stdout:
            encoders.append('h264_nvenc')
    except:
        pass
        
    # Check for VA-API (Intel/AMD on Linux)
    try:
        if os.path.exists('/dev/dri'):
            encoders.append('h264_vaapi')
    except:
        pass
    
    # Check for QuickSync (Intel)
    try:
        qsv = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if 'h264_qsv' in qsv.stdout:
            encoders.append('h264_qsv')
    except:
        pass
        
    # If no HW encoders found, use libx264 (CPU)
    if not encoders:
        encoders.append('libx264')
        
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
        self.manual_rotation = 90  # Default manual rotation for videos (90 degrees)
        self.preview_file = None  # Path to cached preview
        self.preview_status = "none"  # none, generating, ready, error
        self.item_id = str(uuid.uuid4())[:8]  # Unique ID for this item

    def get_preview_filename(self):
        """Generate a unique filename for preview"""
        name = os.path.basename(self.file_path)
        base, _ = os.path.splitext(name)
        base = base.replace(" ", "_")
        if self.is_image:
            return os.path.join(PREVIEW_DIR, f"{base}_{self.item_id}_d{self.display_duration}_r{self.manual_rotation}.mp4")
        else:
            return os.path.join(PREVIEW_DIR, f"{base}_{self.item_id}_s{self.start_time}_e{self.end_time or self.duration}_r{self.manual_rotation}.mp4")

    def invalidate_preview(self):
        """Mark the preview as invalid"""
        if self.preview_file and os.path.exists(self.preview_file):
            try:
                os.unlink(self.preview_file)
            except Exception as e:
                print(f"Warning: Error deleting preview file: {e}")
        self.preview_file = None
        self.preview_status = "none"

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
                'ffprobe', 
                '-v', 'error', 
                '-print_format', 'json', 
                '-show_format', 
                '-show_streams', 
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed: {result.stderr}")
                
            probe = json.loads(result.stdout)
            
            self.duration = float(probe['format']['duration'])
            self.end_time = self.duration
            
            for stream in probe['streams']:
                if stream['codec_type'] == 'video':
                    self.codec = stream.get('codec_name', 'unknown')
                    self.bit_depth = int(stream.get('bits_per_raw_sample', 8))
                    self.width = int(stream.get('width', 0))
                    self.height = int(stream.get('height', 0))
                    self.pixel_format = stream.get('pix_fmt', 'yuv420p')
                    
                    # Handle rotation metadata
                    if 'tags' in stream and 'rotate' in stream['tags']:
                        self.rotation = int(stream['tags']['rotate'])
                    
                    # Check for side data rotation
                    if 'side_data_list' in stream:
                        for side_data in stream['side_data_list']:
                            if side_data.get('side_data_type') == 'Display Matrix':
                                # Parse rotation from display matrix
                                rotation_data = side_data.get('rotation', '')
                                if rotation_data:
                                    try:
                                        # Handle both string and numeric rotation values
                                        if isinstance(rotation_data, (int, float)):
                                            rotation_val = float(rotation_data)
                                        else:
                                            # Assume it's a string like "-90.00 degrees"
                                            rotation_val = float(rotation_data.split()[0])
                                            
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
                'ffprobe', 
                '-v', 'error', 
                '-print_format', 'json', 
                '-show_format', 
                '-show_streams', 
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed: {result.stderr}")
                
            probe = json.loads(result.stdout)
            
            for stream in probe['streams']:
                if stream['codec_type'] == 'video':
                    self.width = int(stream.get('width', 0))
                    self.height = int(stream.get('height', 0))
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
            self.end_label = QLabel(f"End: {media_item.end_time if media_item.end_time else media_item.duration:.2f} sec")
            self.end_slider = QSlider(Qt.Horizontal)
            self.end_slider.setRange(0, int(media_item.duration * 1000))
            self.end_slider.setValue(int((media_item.end_time or media_item.duration) * 1000))
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
        buttons = [self.rotation_0_btn, self.rotation_90_btn, self.rotation_180_btn, self.rotation_270_btn]
        
        for btn in buttons:
            btn.setStyleSheet("")
        
        if degrees == 0:
            self.rotation_0_btn.setStyleSheet("background-color: #1abc9c; color: white;")
        elif degrees == 90:
            self.rotation_90_btn.setStyleSheet("background-color: #1abc9c; color: white;")
        elif degrees == 180:
            self.rotation_180_btn.setStyleSheet("background-color: #1abc9c; color: white;")
        elif degrees == 270:
            self.rotation_270_btn.setStyleSheet("background-color: #1abc9c; color: white;")

class ProcessingWorker(QObject):
    """Worker object for video processing"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, object)  # task, result
    error = pyqtSignal(str, str)  # task, error message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._abort = False
    
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
            
            self.progress.emit(10, f"Processing {os.path.basename(media_item.file_path)}...")
            
            if media_item.is_image:
                # Process image preview - low resolution for speed
                cmd = [
                    '-y',
                    '-loop', '1',
                    '-i', media_item.file_path,
                    '-t', str(media_item.display_duration),
                    '-vf', f'scale=480:-2',
                    '-c:v', 'libx264',
                    '-preset', 'ultrafast',
                    '-crf', '30',
                    '-pix_fmt', 'yuv420p',
                    preview_file
                ]
                
                # Add rotation if needed
                if media_item.manual_rotation != 0:
                    cmd[5] = f'rotate={media_item.manual_rotation*math.pi/180},scale=480:-2'
            else:
                # Process video preview - optimize for speed
                cmd = [
                    '-y',
                    '-i', media_item.file_path,
                    '-ss', str(media_item.start_time),
                    '-t', str((media_item.end_time or media_item.duration) - media_item.start_time),
                    '-vf', 'scale=480:-2',
                    '-c:v', 'libx264',
                    '-preset', 'ultrafast',
                    '-crf', '30',
                    '-c:a', 'aac',
                    '-b:a', '64k',
                    '-pix_fmt', 'yuv420p',
                    preview_file
                ]
                
                # Add rotation if needed
                total_rotation = (media_item.rotation + media_item.manual_rotation) % 360
                if total_rotation != 0:
                    if total_rotation == 90:
                        cmd[5] = 'transpose=1,scale=480:-2'
                    elif total_rotation == 180:
                        cmd[5] = 'transpose=2,transpose=2,scale=480:-2'
                    elif total_rotation == 270:
                        cmd[5] = 'transpose=2,scale=480:-2'
            
            # Run ffmpeg process
            process = subprocess.Popen(
                ['ffmpeg'] + cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Process output line by line for progress tracking
            for line in iter(process.stdout.readline, ''):
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
                            current_sec = float(hours) * 3600 + float(minutes) * 60 + float(seconds.replace(',', '.'))
                            total_sec = (media_item.end_time or media_item.duration) - media_item.start_time
                            if media_item.is_image:
                                total_sec = media_item.display_duration
                            if total_sec > 0:
                                progress = min(int((current_sec / total_sec) * 80) + 10, 90)
                                self.progress.emit(progress, f"Processing {os.path.basename(media_item.file_path)}...")
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
                return preview_file
            else:
                media_item.preview_status = "error"
                return "Error: Created preview file is invalid"
                
        except Exception as e:
            self.progress.emit(0, f"Error: {str(e)}")
            media_item.preview_status = "error"
            return f"Error: {str(e)}"
    
    def process_all_clips(self, items):
        """Process all clips for preview - optimized for speed with HW acceleration"""
        try:
            if not items:
                return "No items to process"
                
            # Use libx264 for maximum compatibility
            best_encoder = 'libx264'
            
            self.progress.emit(5, f"Using CPU encoding for maximum compatibility")
                
            # Process each item
            valid_files = []
            total_items = len(items)
            
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
                    
                self.progress.emit(int((i / total_items) * 70) + 5, 
                                  f"Processing item {i+1}/{total_items}...")
                
                # Use the full duration as specified by user edits
                if not media_item.is_image:
                    # For video - use full edited duration
                    preview_duration = (media_item.end_time or media_item.duration) - media_item.start_time
                else:
                    # For images - use full display duration 
                    preview_duration = media_item.display_duration
                    
                # Create a temporary preview for this item
                temp_preview = os.path.join(TEMP_DIR, f"temp_preview_{i}_{uuid.uuid4().hex[:8]}.mp4")
                
                # Base command for both image and video - use simpler approach
                base_cmd = ['ffmpeg', '-y', '-v', 'error']
                
                # For images
                if media_item.is_image:
                    cmd = base_cmd + [
                        '-loop', '1',
                        '-i', media_item.file_path,
                        '-t', str(preview_duration),
                        '-vf', 'scale=480:-2,fps=24',
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',
                        '-crf', '28',
                        '-pix_fmt', 'yuv420p',
                        '-f', 'mp4',
                        temp_preview
                    ]
                    
                    # Add rotation if needed
                    if media_item.manual_rotation != 0:
                        cmd[9] = f'rotate={media_item.manual_rotation*math.pi/180},scale=480:-2,fps=24'
                else:
                    # For videos - use faster settings but keep full duration 
                    cmd = base_cmd + [
                        '-ss', str(media_item.start_time),
                        '-i', media_item.file_path,
                        '-t', str(preview_duration),
                        '-vf', 'scale=480:-2,fps=24',
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',
                        '-crf', '28',
                        '-c:a', 'aac',  # Include audio for a proper preview
                        '-b:a', '96k',   # Lower audio bitrate for faster processing
                        '-pix_fmt', 'yuv420p',
                        '-f', 'mp4',
                        temp_preview
                    ]
                    
                    # Add rotation if needed
                    total_rotation = (media_item.rotation + media_item.manual_rotation) % 360
                    if total_rotation != 0:
                        rotation_filter = ''
                        if total_rotation == 90:
                            rotation_filter = 'transpose=1,'
                        elif total_rotation == 180:
                            rotation_filter = 'transpose=2,transpose=2,'
                        elif total_rotation == 270:
                            rotation_filter = 'transpose=2,'
                        
                        cmd[8] = f'{rotation_filter}scale=480:-2,fps=24'
                
                # Run command with process monitoring to allow cancellation
                try:
                    self.progress.emit(int((i / total_items) * 60) + 10, 
                                     f"Processing {os.path.basename(media_item.file_path)}...")
                    
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    
                    # Monitor the process with timeout
                    start_time = time.time()
                    max_processing_time = max(60, preview_duration * 2)  # Allow more time for longer clips
                    
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
                            print(f"Processing timeout for {media_item.file_path} after {max_processing_time} seconds")
                            break
                        
                        # Wait a bit before checking again
                        time.sleep(0.1)
                    
                    # Get any error output
                    _, stderr = process.communicate(timeout=1)
                    
                    # Check if file was created successfully
                    if os.path.exists(temp_preview) and os.path.getsize(temp_preview) > 1000:
                        valid_files.append(temp_preview)
                    else:
                        print(f"Error creating preview for {media_item.file_path}: {stderr}")
                except Exception as e:
                    print(f"Error processing file {media_item.file_path}: {str(e)}")
                    # Skip this file on any error
                    pass
            
            if not valid_files:
                return "Failed to create any valid previews"
            
            # Handle case with only one valid file - just return it directly
            if len(valid_files) == 1:
                output_file = os.path.join(TEMP_DIR, f"preview_all_{uuid.uuid4().hex}.mp4")
                try:
                    shutil.copy(valid_files[0], output_file)
                    if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                        self.progress.emit(100, "Preview ready (single clip)")
                        return output_file
                except Exception as e:
                    print(f"Error copying single file: {str(e)}")
            
            # Concatenate all files
            self.progress.emit(80, "Combining all clips...")
            
            # Output file
            output_file = os.path.join(TEMP_DIR, f"preview_all_{uuid.uuid4().hex}.mp4")
            
            # Create a temporary file list for concat
            file_list = os.path.join(TEMP_DIR, f"files_{uuid.uuid4().hex}.txt")
            with open(file_list, 'w') as f:
                for file_path in valid_files:
                    fixed_path = file_path.replace('\\', '/')
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
                'ffmpeg', '-y', '-v', 'error',
                '-f', 'concat',
                '-safe', '0',
                '-i', file_list,
                '-c', 'copy',
                output_file
            ]
            
            # Run with monitoring
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
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
            
            # Check if concat succeeded
            if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                self.progress.emit(100, "Preview ready")
                # Clean up individual files
                for file in valid_files:
                    try:
                        if os.path.exists(file):
                            os.unlink(file)
                    except:
                        pass
                return output_file
            else:
                # If concat failed, try filter_complex method
                self.progress.emit(90, "Trying alternate merge method...")
                
                # Use filter_complex for concatenation
                cmd = ['ffmpeg', '-y', '-v', 'error']
                
                # Add input files (use all files)
                for file in valid_files:
                    cmd.extend(['-i', file])
                
                # Create filter string
                filter_str = ''
                for i in range(len(valid_files)):
                    filter_str += f'[{i}:v]'
                filter_str += f'concat=n={len(valid_files)}:v=1:a=0[outv]'
                
                # Add audio from first file with audio if available
                audio_found = False
                for i, temp_file in enumerate(valid_files):
                    try:
                        audio_info = subprocess.run(
                            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_streams', '-of', 'json', temp_file],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        if '"codec_type":"audio"' in audio_info.stdout:
                            filter_str += f';[{i}:a]acopy[outa]'
                            audio_found = True
                            break
                    except:
                        pass
                
                cmd.extend([
                    '-filter_complex', filter_str,
                    '-map', '[outv]'
                ])
                
                if audio_found:
                    cmd.extend(['-map', '[outa]'])
                
                cmd.extend([
                    '-c:v', 'libx264',
                    '-preset', 'ultrafast',
                    '-crf', '28',
                    '-pix_fmt', 'yuv420p'
                ])
                
                if audio_found:
                    cmd.extend([
                        '-c:a', 'aac',
                        '-b:a', '96k'
                    ])
                
                cmd.append(output_file)
                
                # Run the command with monitoring
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Monitor with timeout
                start_time = time.time()
                while process.poll() is None:
                    # Check for abort
                    if self._abort:
                        process.terminate()
                        # Clean up
                        try:
                            if os.path.exists(output_file):
                                os.unlink(output_file)
                            for file in valid_files:
                                if os.path.exists(file):
                                    os.unlink(file)
                        except:
                            pass
                        return "Aborted"
                        
                    # Check for timeout
                    if time.time() - start_time > 120:  # 2 minutes timeout
                        process.terminate()
                        print("Complex concatenation timed out after 120 seconds")
                        break
                        
                    # Wait a bit
                    time.sleep(0.1)
                
                # Final check
                if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
                    self.progress.emit(100, "Preview ready")
                    # Clean up
                    for file in valid_files:
                        try:
                            if os.path.exists(file):
                                os.unlink(file)
                        except:
                            pass
                    return output_file
                else:
                    # Try one last approach: just return the first clip
                    fallback_file = os.path.join(TEMP_DIR, f"fallback_preview_{uuid.uuid4().hex}.mp4")
                    try:
                        shutil.copy(valid_files[0], fallback_file)
                        self.progress.emit(100, "Preview ready (single clip fallback)")
                        return fallback_file
                    except Exception as e:
                        print(f"Final fallback failed: {str(e)}")
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
            final_encoder = 'libx264'  # Use software encoding for compatibility
            
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
                self.progress.emit(int((i / total_items) * 60) + 5, 
                                f"Processing item {i+1}/{total_items}...")
                
                # Temp file for this item
                temp_file = os.path.join(export_temp, f"part_{i:04d}.mp4")
                
                # Handle images vs videos
                if media_item.is_image:
                    # Image to video
                    cmd = [
                        'ffmpeg', '-y',
                        '-loop', '1',
                        '-i', media_item.file_path,
                        '-t', str(media_item.display_duration),
                        '-c:v', 'libx264',
                        '-preset', 'medium',
                        '-crf', '22',
                        '-pix_fmt', 'yuv420p'
                    ]
                    
                    # Add rotation if needed
                    if media_item.manual_rotation != 0:
                        cmd.extend([
                            '-vf', f'rotate={media_item.manual_rotation*math.pi/180},scale=-2:720'
                        ])
                    else:
                        # Always add scale filter to ensure dimensions are even
                        cmd.extend(['-vf', 'scale=-2:720'])
                    
                    cmd.append(temp_file)
                    
                else:
                    # Video clip
                    cmd = [
                        'ffmpeg', '-y',
                        '-ss', str(media_item.start_time),
                        '-i', media_item.file_path,
                        '-t', str((media_item.end_time or media_item.duration) - media_item.start_time),
                        '-c:v', 'libx264',
                        '-preset', 'medium',
                        '-crf', '22',
                        '-c:a', 'aac',
                        '-b:a', '128k'
                    ]
                    
                    # Add rotation if needed
                    total_rotation = (media_item.rotation + media_item.manual_rotation) % 360
                    if total_rotation != 0:
                        if total_rotation == 90:
                            cmd.extend(['-vf', 'transpose=1,scale=-2:720'])
                        elif total_rotation == 180:
                            cmd.extend(['-vf', 'transpose=2,transpose=2,scale=-2:720'])
                        elif total_rotation == 270:
                            cmd.extend(['-vf', 'transpose=2,scale=-2:720'])
                    else:
                        # Always add scale filter to ensure dimensions are even
                        cmd.extend(['-vf', 'scale=-2:720'])
                
                    cmd.append(temp_file)
                
                # Run the command
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
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
            with open(file_list, 'w') as f:
                for temp_file in temp_files:
                    fixed_path = temp_file.replace('\\', '/')
                    f.write(f"file '{fixed_path}'\n")
            
            # Concatenate all the files
            self.progress.emit(70, "Combining all clips...")
            
            # First try fast concat
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', file_list,
                '-c', 'copy',
                output_path
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
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
            filter_complex = ''
            
            if len(temp_files) == 1:
                # Just one file, copy it with re-encoding
                cmd = [
                    'ffmpeg', '-y',
                    '-i', temp_files[0],
                    '-c:v', 'libx264',
                    '-preset', 'medium',
                    '-crf', '22',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-pix_fmt', 'yuv420p',
                    output_path
                ]
            else:
                # Multiple files
                inputs = []
                for temp_file in temp_files:
                    inputs.extend(['-i', temp_file])
                    
                # Create filter complex for concat
                for i in range(len(temp_files)):
                    filter_complex += f'[{i}:v]'
                filter_complex += f'concat=n={len(temp_files)}:v=1:a=0[outv];'
                
                # Add audio if available (from first file)
                audio_option = []
                for i, temp_file in enumerate(temp_files):
                    try:
                        audio_info = subprocess.run(
                            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_streams', '-of', 'json', temp_file],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        if '"codec_type":"audio"' in audio_info.stdout:
                            filter_complex += f'[{i}:a]aresample=44100[a{i}];'
                            audio_option.extend(['-map', f'[a{i}]'])
                            break
                    except:
                        pass
                
                # Add video map
                filter_complex += f'[outv]scale=-2:720[outv2]'
                
                # Build final command
                cmd = ['ffmpeg', '-y'] + inputs + [
                    '-filter_complex', filter_complex,
                    '-map', '[outv2]'
                ] + audio_option + [
                    '-c:v', 'libx264',
                    '-preset', 'medium',
                    '-crf', '22',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-pix_fmt', 'yuv420p',
                    output_path
                ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
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

class VideoCompilationEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Clean up any old temp files
        cleanup_temp_dirs()
        
        # Setup media player for previews
        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.video_widget = QVideoWidget()
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.stateChanged.connect(self.media_state_changed)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.error.connect(self.handle_player_error)
        
        # Track state
        self.preview_file = None
        self.current_item = None
        self.default_image_duration = 5.0
        self.position_slider_being_dragged = False
        self.progress_dialog = None
        self.is_processing = False
        
        # Create processing thread
        self.processing_thread = ProcessingThread(self)
        self.processing_thread.progress.connect(self.update_progress)
        self.processing_thread.finished.connect(self.processing_finished)
        self.processing_thread.error.connect(self.processing_error)
        
        # Initialize UI
        self.setup_ui()
        
    def setup_ui(self):
        """Set up the user interface"""
        self.setWindowTitle("Video Compilation Editor")
        self.setGeometry(100, 100, 1200, 720)
        self.setMinimumSize(900, 600)
        
        # Apply professional styling
        self.setStyleSheet("""
            QMainWindow, QDialog {
                background-color: #f5f5f5;
            }
            QLabel {
                color: #333333;
            }
            QPushButton {
                background-color: #2c3e50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #34495e;
            }
            QPushButton:pressed {
                background-color: #1abc9c;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
            QListWidget {
                background-color: white;
                border: 1px solid #dcdcdc;
                border-radius: 4px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eeeeee;
            }
            QListWidget::item:selected {
                background-color: #3498db;
                color: white;
            }
            QToolButton {
                background-color: #2c3e50;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QToolButton:hover {
                background-color: #34495e;
            }
            QFrame#statusFrame {
                background-color: #ecf0f1;
                border-radius: 4px;
                padding: 5px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #dcdcdc;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #2c3e50;
                border: none;
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #3498db;
                border-radius: 3px;
            }
            QProgressDialog {
                background-color: #ffffff;
                border: 1px solid #dddddd;
            }
            QProgressDialog QLabel {
                font-size: 14px;
                padding: 8px;
            }
            QProgressDialog QPushButton {
                background-color: #e74c3c;
            }
        """)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Top toolbar
        toolbar = QHBoxLayout()
        
        # Add files buttons
        add_videos_btn = QPushButton("Add Videos")
        add_videos_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogStart))
        add_videos_btn.clicked.connect(self.add_videos)
        
        add_images_btn = QPushButton("Add Images")
        add_images_btn.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        add_images_btn.clicked.connect(self.add_images)
        
        toolbar.addWidget(add_videos_btn)
        toolbar.addWidget(add_images_btn)
        
        # Clip management buttons
        edit_btn = QPushButton("Edit")
        edit_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        edit_btn.clicked.connect(self.edit_selected)
        
        delete_btn = QPushButton("Remove")
        delete_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        delete_btn.clicked.connect(self.delete_selected)
        
        randomize_btn = QPushButton("Shuffle")
        randomize_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        randomize_btn.clicked.connect(self.randomize_order)
        
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(delete_btn)
        toolbar.addWidget(randomize_btn)
        
        # Export button 
        toolbar.addStretch()
        
        preview_all_btn = QPushButton("Preview All")
        preview_all_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        preview_all_btn.clicked.connect(self.preview_all)
        
        export_btn = QPushButton("Export")
        export_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        export_btn.clicked.connect(self.export)
        
        toolbar.addWidget(preview_all_btn)
        toolbar.addWidget(export_btn)
        
        main_layout.addLayout(toolbar)
        
        # Main content splitter
        splitter = QSplitter(Qt.Horizontal)
        
        # Left panel - media list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # Media list header
        list_label = QLabel("Media Items")
        list_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(list_label)
        
        # Media list
        self.clip_list = QListWidget()
        self.clip_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.clip_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.clip_list.itemSelectionChanged.connect(self.selection_changed)
        self.clip_list.itemDoubleClicked.connect(self.edit_selected)
        left_layout.addWidget(self.clip_list)
        
        # Preview button for selected item
        preview_btn = QPushButton("Preview Selected")
        preview_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        preview_btn.clicked.connect(self.preview_selected_item)
        left_layout.addWidget(preview_btn)
        
        # Right panel - preview
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Preview section
        preview_label = QLabel("Preview")
        preview_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(preview_label)
        
        # Add video widget
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout.addWidget(self.video_widget)
        
        # Playback controls
        playback_layout = QHBoxLayout()
        
        # Play button
        self.play_button = QToolButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setIconSize(QSize(24, 24))
        self.play_button.setFixedSize(36, 36)
        self.play_button.clicked.connect(self.play_pause)
        
        # Stop button
        self.stop_button = QToolButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setIconSize(QSize(24, 24))
        self.stop_button.setFixedSize(36, 36)
        self.stop_button.clicked.connect(self.stop)
        
        # Position slider
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.position_slider.sliderPressed.connect(self.slider_pressed)
        self.position_slider.sliderReleased.connect(self.slider_released)
        
        # Time label
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setMinimumWidth(80)
        
        # Add to layout
        playback_layout.addWidget(self.play_button)
        playback_layout.addWidget(self.stop_button)
        playback_layout.addWidget(self.position_slider)
        playback_layout.addWidget(self.time_label)
        
        right_layout.addLayout(playback_layout)
        
        # Add panels to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 700])
        
        main_layout.addWidget(splitter, 1)
        
        # Status bar
        status_frame = QFrame()
        status_frame.setObjectName("statusFrame")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 5, 10, 5)
        
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.status_label)
        
        main_layout.addWidget(status_frame)
    
    def cancel_processing(self):
        """Cancel the current processing operation"""
        if self.processing_thread.isRunning():
            self.processing_thread.abort()
            self.status_label.setText("Processing canceled")

    def media_state_changed(self, state):
        """Handle media player state changes"""
        if state == QMediaPlayer.PlayingState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
    
    def play_pause(self):
        """Toggle play/pause state"""
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()
    
    def stop(self):
        """Stop playback"""
        self.media_player.stop()
    
    def position_changed(self, position):
        """Update position slider and time label"""
        if not self.position_slider_being_dragged:
            self.position_slider.setValue(position)
        
        duration = self.media_player.duration()
        if duration > 0:
            position_sec = position / 1000.0
            duration_sec = duration / 1000.0
            self.time_label.setText(f"{int(position_sec // 60)}:{int(position_sec % 60):02d} / {int(duration_sec // 60)}:{int(duration_sec % 60):02d}")
    
    def duration_changed(self, duration):
        """Update slider range when media duration changes"""
        self.position_slider.setRange(0, duration)
    
    def set_position(self, position):
        """Set media position from slider"""
        self.media_player.setPosition(position)
    
    def slider_pressed(self):
        """Handle slider press event"""
        self.position_slider_being_dragged = True
    
    def slider_released(self):
        """Handle slider release event"""
        self.position_slider_being_dragged = False
        self.media_player.setPosition(self.position_slider.value())
        
    def handle_player_error(self, error):
        """Handle media player errors"""
        if error != QMediaPlayer.NoError:
            self.status_label.setText(f"Media player error: {error}")
            print(f"Media player error: {error}")

    def update_progress(self, value, message):
        """Update progress dialog"""
        if self.progress_dialog:
            self.progress_dialog.setValue(value)
            self.progress_dialog.setLabelText(message)

    def processing_finished(self, task, result):
        """Handle processing thread completion"""
        self.is_processing = False
        
        # Check if it's an error or aborted
        if result == "Aborted":
            self.status_label.setText("Operation canceled")
            return
            
        if isinstance(result, str) and result.startswith("Error"):
            self.status_label.setText("Operation failed")
            QMessageBox.warning(self, "Error", result)
            return
            
        # Success cases - check task
        if task == "preview_item":
            if os.path.exists(result) and os.path.getsize(result) > 1000:
                # Valid preview file
                self.preview_file = result
                self.status_label.setText(f"Playing: {os.path.basename(self.current_item.file_path)}")
                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(result)))
                self.media_player.play()
            else:
                self.status_label.setText("Preview failed - invalid output file")
        
        elif task == "preview_all":
            if os.path.exists(result) and os.path.getsize(result) > 1000:
                # Valid preview file
                self.preview_file = result
                self.status_label.setText("Playing: All items")
                self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(result)))
                self.media_player.play()
            else:
                self.status_label.setText("Preview failed - invalid output file")
        
        elif task == "export":
            if os.path.exists(result) and os.path.getsize(result) > 1000:
                # Valid export file
                self.status_label.setText("Export completed successfully")
                QMessageBox.information(self, "Export Complete", f"The compilation has been saved to:\n{result}")
            else:
                self.status_label.setText("Export failed - invalid output file")
    
    def processing_error(self, task, error_msg):
        """Handle processing thread error"""
        self.is_processing = False
        self.status_label.setText(f"Error during {task}: {error_msg}")
        QMessageBox.warning(self, "Error", f"An error occurred: {error_msg}")

    def play_with_external_player(self, video_file):
        """Play video with system's default player"""
        try:
            if platform.system() == "Windows":
                os.startfile(video_file)
            elif platform.system() == "Darwin":  # macOS
                subprocess.call(('open', video_file))
            else:  # Linux
                # Try different players
                if shutil.which('vlc'):
                    subprocess.Popen(['vlc', video_file])
                elif shutil.which('mpv'):
                    subprocess.Popen(['mpv', video_file])
                else:
                    subprocess.call(('xdg-open', video_file))
            return True
        except Exception as e:
            print(f"Error launching external player: {e}")
            return False
    
    def closeEvent(self, event):
        """Handle window close event"""
        # Stop any media playback
        self.media_player.stop()
        
        # Stop processing thread if running
        if self.processing_thread.isRunning():
            self.processing_thread.abort()
            self.processing_thread.wait()
        
        # Clean up temp files
        cleanup_temp_dirs()
        
        # Accept the event
        event.accept()
    
    def add_videos(self):
        """Add video files to the compilation"""
        files, _ = QFileDialog.getOpenFileNames(
            self, 
            "Select Videos", 
            "", 
            "Video Files (*.mp4 *.avi *.mov *.mkv *.m4v *.webm)"
        )
        
        if not files:
            return
            
        # Show progress dialog for large imports
        if len(files) > 3:
            progress = QProgressDialog("Importing videos...", "Cancel", 0, len(files), self)
            progress.setWindowTitle("Importing")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
        else:
            progress = None
            
        failed_files = []
        
        for i, file_path in enumerate(files):
            if progress:
                progress.setValue(i)
                progress.setLabelText(f"Importing {os.path.basename(file_path)}...")
                QApplication.processEvents()
                if progress.wasCanceled():
                    break
                
            try:
                clip = VideoClip(file_path)
                item = QListWidgetItem(os.path.basename(file_path))
                item.setData(Qt.UserRole, clip)
                self.clip_list.addItem(item)
            except ValueError as e:
                failed_files.append(f"{os.path.basename(file_path)}: {str(e)}")
                
        if progress:
            progress.setValue(len(files))
            
        # Show any errors
        if failed_files:
            error_msg = "The following files could not be imported:\n\n" + "\n".join(failed_files[:5])
            if len(failed_files) > 5:
                error_msg += f"\n\n...and {len(failed_files) - 5} more"
            QMessageBox.warning(self, "Import Errors", error_msg)
            
        # Update status
        if len(files) > len(failed_files):
            self.status_label.setText(f"Added {len(files) - len(failed_files)} videos")
            
            # Select the first added item
            if self.clip_list.count() > 0:
                self.clip_list.setCurrentRow(0)
    
    def add_images(self):
        """Add image files to the compilation"""
        files, _ = QFileDialog.getOpenFileNames(
            self, 
            "Select Images", 
            "", 
            "Image Files (*.jpg *.jpeg *.png *.bmp *.gif *.webp)"
        )
        
        if not files:
            return
            
        # Ask for duration
        dialog = ImageDurationDialog(self, self.default_image_duration)
        if not dialog.exec_():
            return
            
        duration = dialog.duration_spin.value()
        apply_to_all = dialog.apply_to_all.isChecked()
        self.default_image_duration = duration
        
        # Show progress dialog for large imports
        if len(files) > 3:
            progress = QProgressDialog("Importing images...", "Cancel", 0, len(files), self)
            progress.setWindowTitle("Importing")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
        else:
            progress = None
            
        failed_files = []
        
        for i, file_path in enumerate(files):
            if progress:
                progress.setValue(i)
                progress.setLabelText(f"Importing {os.path.basename(file_path)}...")
                QApplication.processEvents()
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
                
        if progress:
            progress.setValue(len(files))
            
        # Show any errors
        if failed_files:
            error_msg = "The following files could not be imported:\n\n" + "\n".join(failed_files[:5])
            if len(failed_files) > 5:
                error_msg += f"\n\n...and {len(failed_files) - 5} more"
            QMessageBox.warning(self, "Import Errors", error_msg)
            
        # Update status
        if len(files) > len(failed_files):
            self.status_label.setText(f"Added {len(files) - len(failed_files)} images")
            
            # Select the first added item
            if self.clip_list.count() > 0:
                self.clip_list.setCurrentRow(0)
    
    def edit_selected(self):
        """Edit properties of the selected item"""
        if not self.current_item:
            QMessageBox.information(self, "No Selection", "Please select a media item to edit.")
            return
            
        # Create edit dialog
        dialog = EditDialog(self, self.current_item)
        if dialog.exec_():
            # Apply changes and invalidate preview
            self.current_item.invalidate_preview()
            self.status_label.setText(f"Updated {os.path.basename(self.current_item.file_path)}")
    
    def randomize_order(self):
        """Shuffle the order of items in the list"""
        if self.clip_list.count() <= 1:
            return
            
        # Get all items
        items = []
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            items.append((item.text(), item.data(Qt.UserRole)))
            
        # Shuffle items
        random.shuffle(items)
        
        # Clear and refill list
        self.clip_list.clear()
        
        for text, data in items:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, data)
            self.clip_list.addItem(item)
            
        self.status_label.setText("Items shuffled")
    
    def delete_selected(self):
        """Remove selected items from the list"""
        if not self.current_item:
            QMessageBox.information(self, "No Selection", "Please select a media item to remove.")
            return
            
        # Find the current item
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            if item.data(Qt.UserRole) == self.current_item:
                # Clean up preview if it exists
                if self.current_item.preview_file and os.path.exists(self.current_item.preview_file):
                    try:
                        os.unlink(self.current_item.preview_file)
                    except:
                        pass
                        
                # Remove from list
                self.clip_list.takeItem(i)
                self.current_item = None
                self.status_label.setText("Item removed")
                return
    
    def selection_changed(self):
        """Handle list selection change"""
        selected_items = self.clip_list.selectedItems()
        
        if selected_items:
            # Get the selected item
            self.current_item = selected_items[0].data(Qt.UserRole)
            
            # Update status to show what's selected
            file_name = os.path.basename(self.current_item.file_path)
            if self.current_item.preview_status == "ready":
                self.status_label.setText(f"Selected: {file_name} (Preview available)")
            else:
                self.status_label.setText(f"Selected: {file_name} (Use Preview button to preview)")
        else:
            self.current_item = None
            self.status_label.setText("Ready")
    
    def preview_selected_item(self):
        """Preview the selected item"""
        if not self.current_item:
            QMessageBox.information(self, "No Selection", "Please select a media item to preview.")
            return
        
        # Check if already processing
        if self.is_processing:
            QMessageBox.information(self, "Processing", "Please wait for the current operation to complete.")
            return
        
        # Check if preview already exists
        if self.current_item.preview_status == "ready" and self.current_item.preview_file and os.path.exists(self.current_item.preview_file):
            # Preview exists, just play it
            self.preview_file = self.current_item.preview_file
            self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.current_item.preview_file)))
            self.media_player.play()
            self.status_label.setText(f"Playing: {os.path.basename(self.current_item.file_path)}")
            return
            
        # No preview exists, need to create one
        self.is_processing = True
        self.current_item.preview_status = "generating"
        self.status_label.setText(f"Creating preview for {os.path.basename(self.current_item.file_path)}...")
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Creating preview...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Preview")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)  # Show after 400ms
        self.progress_dialog.setValue(0)
        
        # Set up processing thread
        self.processing_thread.setup_task("preview_item", [self.current_item])
        
        # Start thread
        self.processing_thread.start()
    
    def preview_all(self):
        """Preview all items in the compilation"""
        # Check if we have items
        if self.clip_list.count() == 0:
            QMessageBox.information(self, "No Items", "Please add some media items first.")
            return
            
        # Check if already processing
        if self.is_processing:
            QMessageBox.information(self, "Processing", "Please wait for the current operation to complete.")
            return
            
        # Get all items
        items = []
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            items.append(item.data(Qt.UserRole))
            
        # Start processing
        self.is_processing = True
        self.status_label.setText("Creating full preview...")
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Creating preview...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Preview")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)  # Show after 400ms
        self.progress_dialog.setValue(0)
        
        # Set up processing thread
        self.processing_thread.setup_task("preview_all", [items])
        
        # Start thread
        self.processing_thread.start()
    
    def export(self):
        """Export the final compilation"""
        # Check if we have items
        if self.clip_list.count() == 0:
            QMessageBox.information(self, "No Items", "Please add some media items first.")
            return
            
        # Check if already processing
        if self.is_processing:
            QMessageBox.information(self, "Processing", "Please wait for the current operation to complete.")
            return
            
        # Get output path
        output_path, _ = QFileDialog.getSaveFileName(
            self, 
            "Save Compilation", 
            "", 
            "Video Files (*.mp4)"
        )
        
        if not output_path:
            return
            
        # Ensure extension
        if not output_path.lower().endswith('.mp4'):
            output_path += '.mp4'
            
        # Get all items
        items = []
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            items.append(item.data(Qt.UserRole))
            
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Exporting video...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Export")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.setMinimumDuration(400)  # Show after 400ms
        self.progress_dialog.setValue(0)
        
        # Start processing
        self.is_processing = True
        self.status_label.setText("Exporting...")
        
        # Set up processing thread
        self.processing_thread.setup_task("export", [items, output_path])
        
        # Start thread
        self.processing_thread.start()

# Add the main execution point at the end of the file
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoCompilationEditor()
    window.show()
    sys.exit(app.exec_())
