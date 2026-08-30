# Sundarikku Pottu Thoduna — PottuAI Cheat with Oracle

PottuAI is a camera-based Onam game that guides a red marker toward a player's forehead and turns the completed hand path into a playful Malayalam horoscope.

The current application is implemented in `pottuai.py`. OpenCV handles gameplay, Gemini Live provides cached Malayalam direction audio, Gemini generates the final Oracle reading, and PySide6 displays the result in a full-screen Malayalam UI. An optional ESP32 can receive the same movement commands over USB serial.

The configured models are `gemini-3.1-flash-live-preview` for Malayalam audio and `gemini-3.7-flash` for the multimodal Oracle reading.

## Features

- Detects the largest visible face with OpenCV's bundled Haar cascade.
- Estimates the forehead target from the detected face rectangle.
- Tracks a saturated red marker in HSV colour space.
- Generates `LEFT`, `RIGHT`, `UP`, `DOWN`, and `STOP` commands.
- Sends changed commands to an optional ESP32 at `115200` baud.
- Uses Gemini Live to generate Malayalam direction clips and caches them locally for low-latency playback.
- Records the marker path internally without displaying the trajectory during gameplay.
- Measures completion time, path length, path efficiency, command counts, and direction reversals.
- Sends the clean final frame, a private path visualization, telemetry, and recent results to Gemini for a unique Malayalam horoscope.
- Reads the horoscope aloud and displays it in a full-screen PySide6 result window.
- Keeps up to six recent readings in a local JSON history file.
- Supports replaying another round without restarting the application.

## How it works

```text
USB camera
    |
    +--> Haar face detection --> forehead target
    |
    +--> HSV red-marker tracking --> marker position
                                      |
target + marker --> verified direction + path telemetry
                         |                    |
                         |                    +--> Gemini Oracle
                         |                         + private path image
                         |                         + Malayalam horoscope
                         |                         + PySide6 result UI
                         |
                         +--> cached Gemini Malayalam audio
                         +--> optional ESP32 command over serial
```

The camera and OpenCV window are closed before the Oracle result window opens. The marker trajectory is never drawn on the public gameplay or result view; it is rendered only into the private diagnostic image used for the Oracle request.

## Requirements

- Python 3.10 or newer
- A USB camera
- A Gemini API key
- An internet connection for initial direction-audio generation and each Oracle reading
- `paplay` (PulseAudio/PipeWire) or `aplay` (ALSA) for spoken audio
- A Malayalam font, preferably Noto Sans Malayalam
- Optional: an ESP32 connected over USB serial

## Raspberry Pi setup

Install the system packages:

```bash
sudo apt update
sudo apt install -y \
  python3-venv \
  pulseaudio-utils \
  alsa-utils \
  fonts-noto-core \
  fonts-noto-extra \
  libgl1
```

Create a virtual environment and install the Python dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Set the Gemini API key and start the app:

```bash
export GEMINI_API_KEY="your-api-key"
python pottuai.py
```

On the first run, PottuAI asks Gemini Live to create five short Malayalam direction clips. It stores the raw PCM files in `.pottuai_gemini_audio/` and reuses them on later runs.

## Configuration

All configuration is optional except `GEMINI_API_KEY` for Gemini voice and Oracle generation.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | unset | Enables Gemini direction audio and Oracle generation. |
| `POTTU_CAMERA` | `0` | OpenCV camera index. |
| `POTTU_SERIAL_PORT` | unset | ESP32 device, such as `/dev/ttyUSB0`; serial is disabled when unset. |
| `POTTU_TARGET_TOLERANCE` | `30` | Target radius in pixels at which `STOP` is issued. |
| `POTTU_AXIS_TOLERANCE` | `20` | Pixel dead zone used to choose the movement axis. |
| `POTTU_GUIDANCE_COOLDOWN` | `1.3` | Seconds before repeating an unchanged spoken command. |
| `POTTU_RED_MIN_AREA` | `500` | Minimum accepted red contour area in pixels. |
| `POTTU_AUDIO_SINK` | automatic | PulseAudio/PipeWire sink name; useful for selecting a Bluetooth device. |
| `POTTU_GEMINI_AUDIO_CACHE` | `.pottuai_gemini_audio` | Directory for cached 24 kHz mono PCM direction clips. |
| `POTTU_HISTORY_FILE` | `pottuai_history.json` | JSON file that stores the six most recent Oracle results. |

Example:

```bash
POTTU_CAMERA=1 \
POTTU_SERIAL_PORT=/dev/ttyACM0 \
POTTU_AUDIO_SINK=bluez_output.YOUR_DEVICE_NAME \
python pottuai.py
```

## Gameplay and controls

1. Keep one face clearly visible to the camera.
2. Move a sufficiently large, saturated red marker toward the yellow forehead target.
3. Follow the English on-screen command or the spoken Malayalam direction.
4. When the marker enters the target radius, the app sends `STOP`, closes the camera view, and opens the Oracle result.

Controls:

- `Q` or `Esc` quits from the camera window or result window.
- `R` restarts from the result window after the Oracle response is ready.
- The Malayalam buttons in the result window restart or exit the app.

If Gemini generation fails or the API key is missing, the result UI uses a deterministic Malayalam fallback horoscope. Spoken guidance requires either previously cached direction clips or a valid API key; Oracle narration requires a valid API key and a successful audio response.

## ESP32 serial integration

Set `POTTU_SERIAL_PORT` to enable serial output. PottuAI sends newline-terminated command strings only when the command changes:

```text
LEFT\n
RIGHT\n
UP\n
DOWN\n
STOP\n
```

The ESP32 firmware should listen at `115200` baud and map these commands to the project hardware. PottuAI sends `STOP` before closing the serial connection. List available ports with:

```bash
python -m serial.tools.list_ports
```

Use a regulated external supply for servos and connect the ESP32 and servo-supply grounds together. Do not power servos directly from Raspberry Pi GPIO.

## Bluetooth/audio output

Run PottuAI as the signed-in desktop user rather than with `sudo`. It prefers a detected Bluetooth PulseAudio/PipeWire sink, then the default PulseAudio/PipeWire sink, and finally the default ALSA device.

Inspect available sinks with:

```bash
pactl info
pactl list short sinks
```

Select a particular sink with `POTTU_AUDIO_SINK` if multiple Bluetooth devices are connected.

## Project structure

```text
.
├── pottuai.py          # Current Oracle Edition application
├── requirements.txt    # Runtime Python dependencies
├── images/             # Prototype development photos
├── models/             # Earlier face-model resources (not used by pottuai.py)
└── archive/            # Previous application iterations
```

## Troubleshooting

If the camera cannot open, verify the index with `POTTU_CAMERA` and make sure no other program is using it. On Linux, `pottuai.py` opens the camera through V4L2.

If `cv2.CascadeClassifier` or `cv2.imshow` is unavailable, remove conflicting or headless OpenCV distributions and reinstall the requirements:

```bash
python -m pip uninstall -y \
  cv2 \
  opencv-python \
  opencv-python-headless \
  opencv-contrib-python \
  opencv-contrib-python-headless
python -m pip install --no-cache-dir --force-reinstall -r requirements.txt
python -c "import cv2; print(cv2.__version__, cv2.CascadeClassifier)"
```

If Malayalam text renders as boxes, install the Noto font packages above and restart the application. If no sound is heard, confirm that either `paplay` or `aplay` is installed and test the selected sink outside PottuAI.

## Development photos

### Phase 1

<table>
  <tr>
    <td width="50%"><img src="images/p1image1.jpg" alt="Testing the Phase 1 guidance prototype" width="100%"></td>
    <td width="50%"><img src="images/p1image2.jpg" alt="Testing directional feedback from the camera application" width="100%"></td>
  </tr>
  <tr>
    <td width="50%"><img src="images/p1image3.jpg" alt="Team developing the Phase 1 hardware and software" width="100%"></td>
    <td width="50%"><img src="images/p1image4.jpg" alt="Developing the connected hardware prototype" width="100%"></td>
  </tr>
</table>

### Phase 2

<table>
  <tr>
    <td width="50%"><img src="images/p2img1.jpeg" alt="Raspberry Pi 5 edge-processing hardware" width="100%"></td>
    <td width="50%"><img src="images/p2img2.jpeg" alt="Motorized camera mount and control electronics" width="100%"></td>
  </tr>
  <tr>
    <td width="50%"><img src="images/p2img3.jpeg" alt="Testing the camera movement setup" width="100%"></td>
    <td width="50%"><img src="images/p2img4.jpeg" alt="Calibrating a micro servo" width="100%"></td>
  </tr>
</table>
