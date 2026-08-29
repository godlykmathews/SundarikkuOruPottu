# Sundarikku Pottu Thoduna — Phase 1

An offline computer-vision prototype that guides a user toward a forehead target. The application detects the face, estimates the pottu position, tracks a red marker-tipped stick, and provides visual and spoken movement instructions.

Phase 1 focuses on validating the complete interaction using a USB camera, OpenCV, and edge-device-friendly processing. The target deployment platform is a Raspberry Pi 5.

## Phase 1 Features

- Detects a face and estimates the forehead target from the eye positions.
- Tracks a small, saturated red marker tip.
- Rejects large skin-coloured regions and sudden marker jumps.
- Generates `LEFT`, `RIGHT`, `UP`, `DOWN`, `STOP`, and `PERFECT` guidance.
- Displays the target, marker position, and direction on the camera feed.
- Provides offline voice feedback using `espeak` on Linux.
- Uses V4L2 for USB-camera capture on Raspberry Pi/Linux.

## Tech Stack

- Python
- OpenCV
- NumPy
- YuNet face detection model in ONNX format
- V4L2 USB-camera interface
- eSpeak for offline voice guidance

## How It Works

```text
USB camera
    |
    +--> YuNet face detection --> eye midpoint --> forehead target
    |
    +--> HSV red-tip tracking ------------------> marker position
                                                     |
forehead target + marker position --> controller --> direction + voice
```

## Project Structure

```text
.
├── main.py             # Main camera and processing loop
├── camera.py           # USB-camera capture
├── face_detector.py    # YuNet face and eye detection
├── target.py           # Forehead target calculation and smoothing
├── pen_tracker.py      # Red marker-tip detection and tracking
├── controller.py       # Movement instructions and success detection
├── audio.py            # Offline voice output
├── config.py           # Camera and detection settings
├── models/             # Computer-vision model files
├── images/             # Phase 1 development photos
└── requirements.txt    # Python dependencies
```

## Raspberry Pi 5 Setup

Connect a USB camera, then install the system and Python dependencies:

```bash
sudo apt update
sudo apt install -y python3-venv espeak libgl1

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run the prototype:

```bash
python main.py
```

Press `Q` in the camera window to exit.

## Working on the Project

<table>
  <tr>
    <td width="50%">
      <img src="images/p1image1.jpg" alt="Testing the Phase 1 guidance prototype" width="100%">
    </td>
    <td width="50%">
      <img src="images/p1image2.jpg" alt="Testing directional feedback from the camera application" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Testing the target and marker guidance</sub></td>
    <td align="center"><sub>Validating real-time directional feedback</sub></td>
  </tr>
  <tr>
    <td width="50%">
      <img src="images/p1image3.jpg" alt="Team developing the Phase 1 hardware and software" width="100%">
    </td>
    <td width="50%">
      <img src="images/p1image4.jpg" alt="Developing the connected hardware prototype" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Integrating the software and electronics</sub></td>
    <td align="center"><sub>Building and testing the hardware controls</sub></td>
  </tr>
</table>

## Phase 1 Status

The end-to-end prototype is working. Current development is focused on improving marker stability under different lighting conditions and optimizing camera processing for Raspberry Pi 5 deployment.

