import sys
import os
import random
import tempfile
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
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import Qt, QUrl
from moviepy import VideoFileClip, concatenate_videoclips  # MoviePy 2.x


class VideoClip:
    def __init__(self, file_path):
        try:
            self.file_path = file_path
            self.duration = VideoFileClip(file_path).duration
            self.start_time = 0
            self.end_time = self.duration
        except Exception as e:
            raise ValueError(f"Failed to load video {file_path}: {e}")


class VideoCompilationEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.media_player = QMediaPlayer()
        self.video_widget = QVideoWidget()
        self.media_player.setVideoOutput(self.video_widget)
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Video Compilation Editor")
        self.setGeometry(100, 100, 800, 600)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()

        # Toolbar with buttons
        toolbar_layout = QHBoxLayout()
        add_videos_btn = QPushButton("Add Videos")
        add_videos_btn.clicked.connect(self.add_videos)
        randomize_btn = QPushButton("Randomize Order")
        randomize_btn.clicked.connect(self.randomize_order)
        delete_btn = QPushButton("Delete Selected")  # New delete button
        delete_btn.clicked.connect(self.delete_selected)
        preview_btn = QPushButton("Preview")
        preview_btn.clicked.connect(self.preview)
        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self.export)
        toolbar_layout.addWidget(add_videos_btn)
        toolbar_layout.addWidget(randomize_btn)
        toolbar_layout.addWidget(delete_btn)
        toolbar_layout.addWidget(preview_btn)
        toolbar_layout.addWidget(export_btn)
        main_layout.addLayout(toolbar_layout)

        # Split layout for clip list and video/trim controls
        split_layout = QHBoxLayout()
        self.clip_list = QListWidget()
        self.clip_list.setDragDropMode(
            QAbstractItemView.InternalMove
        )  # Enable drag-and-drop reordering
        self.clip_list.itemSelectionChanged.connect(self.update_clip_details)
        split_layout.addWidget(self.clip_list, 1)

        right_layout = QVBoxLayout()
        self.trim_controls = QWidget()
        trim_layout = QVBoxLayout()
        self.start_label = QLabel("Start Time: 0.00 sec")
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.valueChanged.connect(self.update_start_time)
        self.end_label = QLabel("End Time: 0.00 sec")
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.valueChanged.connect(self.update_end_time)
        trim_layout.addWidget(QLabel("Trim Selected Clip:"))
        trim_layout.addWidget(self.start_label)
        trim_layout.addWidget(self.start_slider)
        trim_layout.addWidget(self.end_label)
        trim_layout.addWidget(self.end_slider)
        self.trim_controls.setLayout(trim_layout)
        self.trim_controls.setVisible(False)
        right_layout.addWidget(self.trim_controls)
        right_layout.addWidget(self.video_widget, 2)
        split_layout.addLayout(right_layout, 2)

        main_layout.addLayout(split_layout)

        # Instructions label
        instructions = QLabel(
            "Instructions: Drag and drop clips to reorder. Select a clip to trim. Use buttons to add, delete, or randomize clips."
        )
        main_layout.addWidget(instructions)

        central_widget.setLayout(main_layout)

    def add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Videos", "", "Video Files (*.mp4 *.avi *.mov)"
        )
        for file in files:
            try:
                clip = VideoClip(file)
                item = QListWidgetItem(os.path.basename(file))
                item.setData(Qt.UserRole, clip)
                self.clip_list.addItem(item)
            except ValueError as e:
                QMessageBox.warning(self, "Invalid Video", str(e))

    def randomize_order(self):
        items = [self.clip_list.takeItem(0) for _ in range(self.clip_list.count())]
        random.shuffle(items)
        for item in items:
            self.clip_list.addItem(item)

    def delete_selected(self):
        for item in self.clip_list.selectedItems():
            self.clip_list.takeItem(self.clip_list.row(item))

    def update_clip_details(self):
        selected_items = self.clip_list.selectedItems()
        if selected_items:
            item = selected_items[0]
            clip = item.data(Qt.UserRole)
            self.trim_controls.setVisible(True)
            self.start_slider.setRange(0, int(clip.duration * 1000))
            self.end_slider.setRange(0, int(clip.duration * 1000))
            self.start_slider.setValue(int(clip.start_time * 1000))
            self.end_slider.setValue(int(clip.end_time * 1000))
            self.start_label.setText(f"Start Time: {clip.start_time:.2f} sec")
            self.end_label.setText(f"End Time: {clip.end_time:.2f} sec")
        else:
            self.trim_controls.setVisible(False)

    def update_start_time(self, value):
        if self.clip_list.selectedItems():
            item = self.clip_list.selectedItems()[0]
            clip = item.data(Qt.UserRole)
            clip.start_time = value / 1000.0
            self.start_label.setText(f"Start Time: {clip.start_time:.2f} sec")

    def update_end_time(self, value):
        if self.clip_list.selectedItems():
            item = self.clip_list.selectedItems()[0]
            clip = item.data(Qt.UserRole)
            clip.end_time = value / 1000.0
            self.end_label.setText(f"End Time: {clip.end_time:.2f} sec")

    def preview(self):
        try:
            if self.clip_list.count() == 0:
                QMessageBox.warning(self, "No Clips", "Please add clips to preview.")
                return
            clips = self.get_trimmed_clips()
            if clips is None:
                return
            final_clip = concatenate_videoclips(clips)
            temp_file = tempfile.NamedTemporaryFile(
                delete=False, suffix=".mp4"
            )  # New temp file each time
            final_clip.write_videofile(temp_file.name, codec="libx264")
            self.media_player.setMedia(
                QMediaContent(QUrl.fromLocalFile(temp_file.name))
            )
            self.media_player.play()
        except Exception as e:
            QMessageBox.critical(self, "Preview Error", f"Failed to preview: {e}")

    def export(self):
        try:
            if self.clip_list.count() == 0:
                QMessageBox.warning(self, "No Clips", "Please add clips to export.")
                return
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Compilation", "", "Video Files (*.mp4)"
            )
            if save_path:
                clips = self.get_trimmed_clips()
                if clips is None:
                    return
                final_clip = concatenate_videoclips(clips)
                final_clip.write_videofile(save_path, codec="libx264")
                QMessageBox.information(
                    self, "Export Complete", "The compilation has been saved."
                )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def get_trimmed_clips(self):
        clips = []
        for i in range(self.clip_list.count()):
            item = self.clip_list.item(i)
            clip = item.data(Qt.UserRole)
            if clip.start_time >= clip.end_time:
                QMessageBox.warning(
                    self,
                    "Invalid Trim",
                    f"Clip {os.path.basename(clip.file_path)} has invalid trim times.",
                )
                return None
            try:
                video = VideoFileClip(clip.file_path).subclipped(
                    clip.start_time, clip.end_time
                )  # MoviePy 2.x
                clips.append(video)
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Clip Error",
                    f"Failed to process {os.path.basename(clip.file_path)}: {e}",
                )
                return None
        return clips


if __name__ == "__main__":
    app = QApplication(sys.argv)
    editor = VideoCompilationEditor()
    editor.show()
    sys.exit(app.exec_())
