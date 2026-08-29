# Sundarikku Pottu Thoduna

An offline computer-vision prototype that guides a user toward a forehead target. The application detects the face, estimates the pottu position, tracks a red marker-tipped stick, and provides visual and spoken movement instructions.

Phase 1 focuses on validating the complete interaction using a USB camera, OpenCV, and edge-device-friendly processing. The target deployment platform is a Raspberry Pi 5.

Phase 2 adds a motorized pan-and-tilt camera prototype. Face-center coordinates are sent to an ESP32 so that two servos can move the camera and keep the user in view while the guidance system runs.

## Phase 1 Features

- Detects a face and estimates the forehead target from the eye positions.
- Tracks a small, saturated red marker tip.
- Rejects large skin-coloured regions and sudden marker jumps.
- Generates `LEFT`, `RIGHT`, `UP`, `DOWN`, `STOP`, and `PERFECT` guidance.
- Displays the target, marker position, and direction on the camera feed.
- Provides offline voice feedback using `espeak` on Linux.
- Uses V4L2 for USB-camera capture on Raspberry Pi/Linux.

## Phase 2 Update

Phase 2 moves the project from a fixed camera to a hardware-tracking setup. The current prototype combines a Raspberry Pi 5, a USB camera mounted on a two-axis servo rig, and an ESP32 movement controller.

- Detects the center of the user's face from the live camera feed.
- Sends the face-center coordinates to the ESP32 over USB serial.
- Uses separate pan and tilt servos for horizontal and vertical camera movement.
- Keeps red-marker tracking and offline spoken hand guidance in the same application.
- Adds movement calibration, a center dead zone, and safe servo travel limits to the hardware-testing workflow.

## Tech Stack

- Python
- OpenCV
- NumPy
- Haar cascade and YuNet face-detection resources
- PySerial for Raspberry Pi-to-ESP32 communication
- ESP32 with a two-axis servo camera mount
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

The Phase 2 camera-tracking path is:

```text
USB camera --> face detection --> face-center X/Y
                                      |
                                      v
                              USB serial at 115200 baud
                                      |
                                      v
                               ESP32 movement control
                                      |
                                      v
                         pan servo + tilt servo --> camera movement
```

## Project Structure

```text
.
├── model.py            # Active vision, guidance, audio, and serial prototype
├── models/             # Computer-vision model resources
├── images/             # Phase 1 and Phase 2 development photos
└── requirements.txt    # Python dependencies
```

## Raspberry Pi 5 Setup

Connect a USB camera, then install the system and Python dependencies:

```bash
sudo apt update
sudo apt install -y python3-venv espeak pulseaudio-utils libgl1

python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If OpenCV is incomplete or `cv2.CascadeClassifier` is missing, clean all
conflicting OpenCV packages once and reinstall the requirements:

```bash
python -m pip uninstall -y cv2 opencv-python opencv-python-headless \
  opencv-contrib-python opencv-contrib-python-headless
python -m pip install --no-cache-dir --force-reinstall -r requirements.txt
python -c "import cv2; print(cv2.__version__, cv2.CascadeClassifier)"
```

For later reinstalls, only the second and third commands are needed.

Run the prototype:

```bash
python model.py
```

Press `Q` in the camera window to exit.

### Phase 2 hardware tracking setup

The hardware prototype uses:

- A Raspberry Pi 5 or development computer running `model.py`.
- A USB camera attached to a two-axis pan-and-tilt bracket.
- An ESP32 connected to the computer over USB.
- Two hobby servos: one for horizontal pan and one for vertical tilt.
- A suitable regulated servo power supply, jumper wires, and a shared ground between the servo supply and ESP32.

Mount the camera securely on the tilt stage, then mount that assembly on the pan stage. Center both servos before attaching their horns so that the camera begins near the middle of its mechanical range. Connect the servo signal wires to the pins configured in the ESP32 firmware. Do not power both servos directly from a Raspberry Pi GPIO pin; use a suitable servo supply and connect the grounds together.

The ESP32 firmware should listen at `115200` baud for the space-separated face coordinates sent by `model.py`:

```text
X Y\n
```

The serial connection is disabled in the current checkout until a device port is selected. In the ESP32 initialization block in `model.py`, replace the disabled placeholder with the port detected on your system, for example:

```python
# Raspberry Pi/Linux example; the actual device may also be /dev/ttyACM0
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)

# Windows example
# ser = serial.Serial("COM3", 115200, timeout=1)
```

List available serial ports with:

```bash
python -m serial.tools.list_ports
```

### Camera movement setup

Calibrate the movement before enabling full face tracking:

1. Test the pan and tilt axes separately and reverse an axis in the ESP32 firmware if it moves away from the detected face.
2. Set minimum and maximum servo angles that stop before the mount reaches a mechanical limit.
3. Define the center of the camera frame and use a small dead zone around it to prevent constant servo jitter.
4. Begin with small angle steps, then tune the response until the camera follows a moving face smoothly.
5. Run `python model.py`, move in front of the camera, and confirm that the ESP32 receives `X Y` values before allowing both axes to move.

The Python side currently sends coordinates when a serial connection is active. ESP32 movement firmware and its GPIO pin assignments must match the specific pan-and-tilt hardware being used.

### Bluetooth headset output

Pair and connect the headset in Raspberry Pi OS before starting the app. The
application automatically prefers a connected Bluetooth audio sink; if none is
available, it uses the current system default output.

Run the app as the signed-in desktop user (not with `sudo`). Verify that the
audio server is reachable, then list the available output sink names:

```bash
pactl info
pactl list short sinks
```

If more than one Bluetooth headset is connected, select one explicitly:

```bash
POTTU_AUDIO_SINK=bluez_output.YOUR_DEVICE_NAME python model.py
```

## Phase 1 Development

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

## Phase 2 Development

<table>
  <tr>
    <td width="50%">
      <img src="images/p2img1.jpeg" alt="Raspberry Pi 5 edge-processing hardware for the Phase 2 prototype" width="100%">
    </td>
    <td width="50%">
      <img src="images/p2img2.jpeg" alt="Motorized camera tracking mount connected to the control electronics" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Preparing the Raspberry Pi 5 for Phase 2 tracking</sub></td>
    <td align="center"><sub>Assembling the movable camera tracking rig</sub></td>
  </tr>
  <tr>
    <td width="50%">
      <img src="images/p2img3.jpeg" alt="Testing the Phase 2 camera tracking and movement setup" width="100%">
    </td>
    <td width="50%">
      <img src="images/p2img4.jpeg" alt="Calibrating a micro servo for camera movement" width="100%">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Testing camera movement with live face tracking</sub></td>
    <td align="center"><sub>Calibrating servo position and movement limits</sub></td>
  </tr>
</table>

## Project Status

- **Phase 1:** The fixed-camera guidance prototype is working end to end. Ongoing work includes improving marker stability under different lighting conditions.
- **Phase 2:** The Raspberry Pi, ESP32, pan-and-tilt camera mount, and servos are being integrated and tested. Serial communication is present in `model.py` but remains disabled until the correct device port is configured; movement calibration and safe servo limits are still in progress.
