# Historian - Video Compilation Editor

A professional desktop application for creating video compilations from videos and images with background music and various effects.

## Overview

Historian is a powerful, lightning fast, yet user-friendly video compilation editor built with Python and PyQt5. It allows users to combine multiple video clips and images into a single compilation with smooth transitions, custom durations, effects, and background music. Perfect for creating montages, slideshows, and compilation videos without the complexity of professional video editing software.

## Features

- **Multi-format Support**: Import various video formats and image types
- **Timeline Editor**: Visual timeline with drag-and-drop reordering
- **Video Trimming**: Set custom start and end times for video clips
- **Image Duration**: Configure display duration for static images
- **Video Effects**: Apply various effects including:
  - Speed adjustment
  - Rotation
  - Filters (blur, sharpen, etc.)
  - Visual effects
- **Background Music**: Add and configure multiple audio tracks with control over:
  - Volume adjustment
  - Start time in compilation
  - Start time in audio track
  - Duration
- **Smart Previews**: Preview individual clips or the entire compilation
- **Hardware Acceleration**: Auto-detection of hardware encoders for faster processing
- **High-quality Export**: Export to MP4 with configurable quality settings

## Requirements

### Software Dependencies

- Python 3.6+
- PyQt5 (GUI framework)
- FFmpeg (for video processing)

### Python Packages

- PyQt5==5.15.11
- ffmpeg-python==0.2.0

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/historian.git
   cd historian
   ```

2. Create a virtual environment (recommended):
   ```
   python -m venv .venv
   ```

3. Activate the virtual environment:
   - Windows: `.venv\Scripts\activate`
   - macOS/Linux: `source .venv/bin/activate`

4. Install required packages:
   ```
   pip install -r requirments.txt
   ```

5. Install FFmpeg:
   - Windows: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH
   - macOS: `brew install ffmpeg`
   - Ubuntu/Debian: `sudo apt install ffmpeg`
   - Fedora: `sudo dnf install ffmpeg`

## Usage

Run the application with:

```
python video_compilation_editor.py
```

### Basic Workflow

1. **Add Media**:
   - Click "Add Videos" to import video files
   - Click "Add Images" to import still images

2. **Arrange Content**:
   - Drag and drop items in the list to reorder
   - Use "Shuffle" for random ordering

3. **Edit Items**:
   - Select an item and click "Edit"
   - For videos: adjust start/end time, rotation, and speed
   - For images: set duration and rotation
   - Add effects through the Effects button

4. **Add Background Music**:
   - Click "Add Music" to import audio files
   - Adjust volume, timing, and fades

5. **Preview**:
   - Use "Preview Selected" to review a single item
   - Use "Preview All" to see the entire compilation

6. **Export**:
   - Click "Export" and select output location and quality settings

## Architecture

The application is structured with several key components:

- **VideoCompilationEditor**: Main application window and controller
- **MediaItem**: Base class for media elements (videos and images)
- **VideoClip** and **ImageItem**: Specific media type implementations
- **ProcessingWorker** and **ProcessingThread**: Background processing with progress reporting
- **TimelineWidget**: Custom widget for visualizing the timeline
- **VideoEffect**: Encapsulates video effect parameters and FFmpeg commands
- **MusicTrack**: Manages audio track properties and timing

The application uses FFmpeg for all video and audio processing, leveraging hardware acceleration when available.

## Technical Details

- **Temporary Files**: Generated previews are stored in a temporary directory
- **Smart Caching**: Preview files are cached to avoid redundant processing
- **Multi-threading**: Heavy processing runs in background threads to keep UI responsive
- **Hardware Detection**: Automatically detects and uses available hardware encoders

## TODO List

- [ ] Fix any file paths with spaces issues in FFmpeg commands
- [ ] Add more video effects and transitions (cross-fade, wipe, etc.)
- [ ] Implement overlay text/subtitles functionality
- [ ] Create proper packaging for easy installation
- [ ] Add support for drag-and-drop from file explorer
- [ ] Implement undo/redo functionality
- [ ] Add timeline zoom controls for more precise editing
- [ ] Create presets for common effect combinations
- [ ] Add export to additional formats (WebM, GIF, etc.)
- [ ] Implement project save/load functionality
- [ ] Add keyboard shortcuts for common operations
- [ ] Create more comprehensive error handling and recovery
- [ ] Improve performance for large compilations
- [ ] Add automatic thumbnail generation for imported media
- [ ] Create detailed documentation and user guide

## Contributing

Contributions are welcome and encouraged!!! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

*Made by Tyler Gilman* 
