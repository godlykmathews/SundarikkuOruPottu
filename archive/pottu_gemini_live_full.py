import atexit
import asyncio
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

import cv2
import numpy as np
import serial

# ============================================================
# PottuAI
# Gemini 3.1 Flash Live Preview -> Malayalam live voice guidance
# Gemini 3.7 Flash              -> final horoscope prediction
#
# IMPORTANT DESIGN:
# - OpenCV computes exact dx/dy and the verified direction.
# - Gemini Live speaks that verified direction in Malayalam.
# - No trajectory/path line is shown on the camera window.
# - Path history is still stored internally for final telemetry.
# - When target tolerance is reached, camera capture stops immediately.
# - Gemini 3.7 Flash analyzes the frozen final frame + telemetry.
# - If Gemini/Internet fails, core game continues with local fallback.
#
# Install:
#   pip install -U google-genai opencv-python numpy pyserial
#
# Raspberry Pi audio:
#   sudo apt install pulseaudio-utils alsa-utils espeak-ng
#
# Environment:
#   export GEMINI_API_KEY="YOUR_KEY"
#
# Optional:
#   export POTTU_CAMERA=0
#   export POTTU_TARGET_TOLERANCE=30
#   export POTTU_AXIS_TOLERANCE=20
#   export POTTU_SERIAL_PORT=/dev/ttyUSB0
#   export POTTU_AUDIO_SINK=bluez_output.XX_XX_XX_XX_XX_XX.1
# ============================================================


# -------------------------
# Configuration
# -------------------------

LIVE_MODEL = "gemini-3.1-flash-live-preview"
GEN_MODEL = "gemini-3.7-flash"

CAMERA_INDEX = int(os.environ.get("POTTU_CAMERA", "0"))
TARGET_TOLERANCE = int(os.environ.get("POTTU_TARGET_TOLERANCE", "30"))
AXIS_TOLERANCE = int(os.environ.get("POTTU_AXIS_TOLERANCE", "20"))

RED_MIN_AREA = 500
PATH_SAMPLE_DISTANCE = 5.0

# Prevent Gemini from speaking the same direction too often.
GUIDANCE_REPEAT_COOLDOWN = float(os.environ.get("POTTU_GUIDANCE_COOLDOWN", "1.4"))

SERIAL_PORT = os.environ.get("POTTU_SERIAL_PORT", "").strip()
SERIAL_BAUD = 115200

WINDOW_NAME = "PottuAI - Gemini Malayalam Guide"

MALAYALAM = {
    "LEFT": "ഇടത്തോട്ട്",
    "RIGHT": "വലത്തോട്ട്",
    "UP": "മുകളിലേക്ക്",
    "DOWN": "താഴേക്ക്",
    "STOP": "നിർത്തൂ",
}


# -------------------------
# Audio playback
# -------------------------

class PCMPlayer:
    """
    Plays raw Gemini Live PCM audio.

    Gemini Live audio output is raw signed 16-bit little-endian mono PCM
    at 24 kHz.
    """

    def __init__(self):
        self.paplay = shutil.which("paplay")
        self.aplay = shutil.which("aplay")
        self.pactl = shutil.which("pactl")
        self.espeak = shutil.which("espeak-ng") or shutil.which("espeak")

        self.sink_override = os.environ.get("POTTU_AUDIO_SINK", "").strip() or None
        self.cached_sink = None
        self.last_sink_check = 0.0

        self._fallback_lock = threading.Lock()
        self._fallback_busy = False

        self._print_status()

    def _find_bluetooth_sink(self):
        if self.sink_override:
            return self.sink_override

        if not self.pactl:
            return None

        try:
            result = subprocess.run(
                [self.pactl, "list", "short", "sinks"],
                capture_output=True,
                text=True,
                check=True,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        bluetooth_sinks = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue

            if "bluez" in line.lower() or "bluetooth" in line.lower():
                bluetooth_sinks.append(fields[1])

        if not bluetooth_sinks:
            return None

        try:
            default_sink = subprocess.run(
                [self.pactl, "get-default-sink"],
                capture_output=True,
                text=True,
                check=True,
                timeout=1.0,
            ).stdout.strip()

            if default_sink in bluetooth_sinks:
                return default_sink
        except (OSError, subprocess.SubprocessError):
            pass

        return bluetooth_sinks[0]

    def get_sink(self, force=False):
        now = time.monotonic()
        if force or now - self.last_sink_check >= 5.0:
            self.last_sink_check = now
            self.cached_sink = self._find_bluetooth_sink()
        return self.cached_sink

    def _print_status(self):
        sink = self.get_sink(force=True)

        if self.paplay:
            if sink:
                print(f"Audio: Pulse/PipeWire Bluetooth sink -> {sink}")
            else:
                print("Audio: Pulse/PipeWire default sink")
        elif self.aplay:
            print("Audio: ALSA default output")
        else:
            print("Audio: no raw PCM player found")

    def play_pcm(self, pcm_bytes):
        if not pcm_bytes:
            return

        sink = self.get_sink()

        if self.paplay:
            cmd = [
                self.paplay,
                "--raw",
                "--rate=24000",
                "--channels=1",
                "--format=s16le",
                "--client-name=PottuAI",
                "--stream-name=Gemini Live Malayalam",
            ]
            if sink:
                cmd.append(f"--device={sink}")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.stdin.write(pcm_bytes)
                proc.stdin.close()
                proc.wait(timeout=5)
                return
            except (OSError, subprocess.SubprocessError, BrokenPipeError):
                pass

        if self.aplay:
            try:
                proc = subprocess.Popen(
                    [
                        self.aplay,
                        "-q",
                        "-t", "raw",
                        "-f", "S16_LE",
                        "-c", "1",
                        "-r", "24000",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.stdin.write(pcm_bytes)
                proc.stdin.close()
                proc.wait(timeout=5)
                return
            except (OSError, subprocess.SubprocessError, BrokenPipeError):
                pass

    def local_fallback(self, command):
        """
        Emergency local fallback.
        eSpeak Malayalam availability varies, so use very short English words.
        """
        if not self.espeak:
            return

        fallback = {
            "LEFT": "left",
            "RIGHT": "right",
            "UP": "up",
            "DOWN": "down",
            "STOP": "stop",
        }.get(command)

        if not fallback:
            return

        with self._fallback_lock:
            if self._fallback_busy:
                return
            self._fallback_busy = True

        def worker():
            try:
                subprocess.run(
                    [self.espeak, "-s", "180", fallback],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                with self._fallback_lock:
                    self._fallback_busy = False

        threading.Thread(target=worker, daemon=True).start()


pcm_player = PCMPlayer()


# -------------------------
# ESP32
# -------------------------

class ESP32Link:
    def __init__(self):
        self.ser = None
        self.last_command = None

        if not SERIAL_PORT:
            print("ESP32: disabled (set POTTU_SERIAL_PORT to enable)")
            return

        try:
            self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
            time.sleep(2)
            print(f"ESP32: connected -> {SERIAL_PORT}")
        except Exception as exc:
            print(f"ESP32: offline ({exc})")
            self.ser = None

    def send(self, command):
        if command == self.last_command:
            return

        self.last_command = command

        if not self.ser or not self.ser.is_open:
            return

        try:
            self.ser.write((command + "\n").encode("utf-8"))
        except Exception as exc:
            print(f"ESP32 write error: {exc}")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.write(b"STOP\n")
                self.ser.close()
        except Exception:
            pass


esp32 = ESP32Link()
atexit.register(esp32.close)


# -------------------------
# Gemini Live Malayalam guide
# -------------------------

class GeminiLiveMalayalamGuide:
    """
    Persistent Gemini Live session on a background asyncio thread.

    IMPORTANT:
    The local controller calculates the verified movement command.
    Gemini receives that exact command and speaks it naturally in Malayalam.

    This means:
    - Gemini is the live Malayalam voice guide.
    - Geometry remains deterministic and safe.
    - A hallucinated opposite direction cannot control the ESP32.
    """

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.enabled = bool(self.api_key)

        self.status = "STARTING" if self.enabled else "OFFLINE"
        self.last_transcript = ""

        # Keep only latest guidance. Stale movement guidance is dangerous/useless.
        self.events = queue.Queue(maxsize=1)

        self.stop_event = threading.Event()
        self.thread = None

        self.last_submitted_command = None
        self.last_submitted_time = 0.0

        if self.enabled:
            self.thread = threading.Thread(
                target=self._thread_main,
                daemon=True,
            )
            self.thread.start()
        else:
            print("Gemini Live: GEMINI_API_KEY missing; fallback speech enabled.")

    def submit(self, command, dx, dy, distance):
        if command not in MALAYALAM:
            return

        now = time.monotonic()

        command_changed = command != self.last_submitted_command
        repeat_due = now - self.last_submitted_time >= GUIDANCE_REPEAT_COOLDOWN

        if not command_changed and not repeat_due:
            return

        self.last_submitted_command = command
        self.last_submitted_time = now

        event = {
            "command": command,
            "dx": int(dx),
            "dy": int(dy),
            "distance": int(distance),
        }

        # If cloud is currently unavailable, do not block gameplay.
        if not self.enabled or self.status == "OFFLINE":
            pcm_player.local_fallback(command)
            return

        # Remove stale queued command.
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass

        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def close(self):
        self.stop_event.set()

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.5)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.status = "OFFLINE"
            print(f"Gemini Live worker stopped: {exc}")

    async def _run(self):
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:
            self.status = "OFFLINE"
            print(f"Gemini Live import failed: {exc}")
            return

        client = genai.Client(api_key=self.api_key)

        system_instruction = """
You are PottuAI's real-time Malayalam voice navigation assistant.

A local OpenCV controller sends you a VERIFIED_COMMAND.
The command is already mathematically verified from dx/dy geometry.

You MUST NOT change the direction.
You MUST NOT reason about another direction.
You MUST NOT add explanations.

Speak ONLY Malayalam and use at most 3 words.

Exact mapping:
LEFT  -> ഇടത്തോട്ട്
RIGHT -> വലത്തോട്ട്
UP    -> മുകളിലേക്ക്
DOWN  -> താഴേക്ക്
STOP  -> നിർത്തൂ

If VERIFIED_COMMAND is STOP, say only:
നിർത്തൂ

No greetings.
No English.
No numbers.
No commentary.
""".strip()

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
            output_audio_transcription={},
            thinking_config=types.ThinkingConfig(
                thinking_level="minimal",
            ),
        )

        while not self.stop_event.is_set():
            try:
                self.status = "CONNECTING"

                async with client.aio.live.connect(
                    model=LIVE_MODEL,
                    config=config,
                ) as session:
                    self.status = "ONLINE"
                    print(f"Gemini Live: connected -> {LIVE_MODEL}")

                    while not self.stop_event.is_set():
                        try:
                            event = await asyncio.to_thread(
                                self.events.get,
                                True,
                                0.25,
                            )
                        except queue.Empty:
                            continue

                        command = event["command"]

                        prompt = (
                            f"VERIFIED_COMMAND={command}\n"
                            f"dx={event['dx']}, dy={event['dy']}, "
                            f"distance={event['distance']}px.\n"
                            f"Speak only the mapped Malayalam command."
                        )

                        # Gemini 3.1 Flash Live requires realtime text updates
                        # through send_realtime_input().
                        await session.send_realtime_input(text=prompt)

                        audio_chunks = []

                        # session.receive() yields one complete model turn.
                        async for response in session.receive():
                            server_content = response.server_content

                            if not server_content:
                                continue

                            # Gemini 3.1 can provide multiple parts in one event.
                            model_turn = server_content.model_turn
                            if model_turn and model_turn.parts:
                                for part in model_turn.parts:
                                    inline_data = part.inline_data
                                    if inline_data and inline_data.data:
                                        audio_chunks.append(inline_data.data)

                            transcript = server_content.output_transcription
                            if transcript and transcript.text:
                                self.last_transcript = transcript.text.strip()
                                if self.last_transcript:
                                    print(
                                        f"Gemini Malayalam [{command}]: "
                                        f"{self.last_transcript}"
                                    )

                        # Play the entire short Malayalam command as one chunk.
                        if audio_chunks:
                            await asyncio.to_thread(
                                pcm_player.play_pcm,
                                b"".join(audio_chunks),
                            )
                        else:
                            pcm_player.local_fallback(command)

            except Exception as exc:
                self.status = "OFFLINE"
                print(f"Gemini Live connection error: {exc}")

                # Gameplay continues while reconnect is attempted.
                await asyncio.sleep(2.0)

                if not self.stop_event.is_set():
                    self.status = "RECONNECTING"


live_guide = GeminiLiveMalayalamGuide()
atexit.register(live_guide.close)


# -------------------------
# Gemini 3.7 horoscope
# -------------------------

class GeminiHoroscope:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.enabled = bool(self.api_key)

        self.status = "READY" if self.enabled else "OFFLINE"
        self.result = None
        self.error = None

        self._busy = False
        self._lock = threading.Lock()

    def start(self, frozen_frame, telemetry):
        with self._lock:
            if self._busy:
                return

            if not self.enabled:
                self.status = "OFFLINE"
                self.result = self._fallback(telemetry)
                return

            self._busy = True
            self.status = "THINKING"
            self.result = None
            self.error = None

        threading.Thread(
            target=self._worker,
            args=(frozen_frame.copy(), dict(telemetry)),
            daemon=True,
        ).start()

    def _worker(self, frame, telemetry):
        try:
            from google import genai
            from google.genai import types

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), 88],
            )

            if not ok:
                raise RuntimeError("Could not encode final OpenCV frame.")

            prompt = f"""
You are the playful PottuAI Oracle for the Kerala Onam game
"Sundarikk Pottu Thodal".

The image is the FINAL FROZEN camera frame at the exact moment the player
reached the target. There is intentionally NO visible trajectory line.

Use the image for visual context, but trust these local measurements as facts:

Final error: {telemetry['final_error']} pixels
Completion time: {telemetry['completion_time']:.2f} seconds
Sampled hidden path points: {telemetry['path_points']}
Hidden path length: {telemetry['path_length']:.1f} pixels
Direction reversals: {telemetry['reversals']}
Commands used: {telemetry['command_counts']}

Generate a fun horoscope-style personality prediction based on how the player moved.
Do NOT invent any additional measurements.
Do NOT claim supernatural certainty.

Return exactly two lines:

TITLE: <2 to 5 word playful title>
PREDICTION: <one playful prediction, maximum 25 words>
""".strip()

            client = genai.Client(api_key=self.api_key)

            response = client.models.generate_content(
                model=GEN_MODEL,
                contents=[
                    types.Part.from_bytes(
                        data=encoded.tobytes(),
                        mime_type="image/jpeg",
                    ),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.85,
                    max_output_tokens=120,
                ),
            )

            text = (response.text or "").strip()

            if not text:
                text = self._fallback(telemetry)

            with self._lock:
                self.result = text
                self.status = "DONE"

            print("\n========== POTTuAI ORACLE ==========")
            print(text)
            print("====================================\n")

        except Exception as exc:
            print(f"Gemini 3.7 horoscope failed: {exc}")

            with self._lock:
                self.error = str(exc)
                self.result = self._fallback(telemetry)
                self.status = "OFFLINE"

        finally:
            with self._lock:
                self._busy = False

    @staticmethod
    def _fallback(telemetry):
        error = telemetry["final_error"]
        reversals = telemetry["reversals"]

        if error <= 8:
            title = "FOREHEAD SNIPER"
        elif reversals <= 2:
            title = "STEADY NAVIGATOR"
        else:
            title = "LUCKY ZIGZAGGER"

        return (
            f"TITLE: {title}\n"
            "PREDICTION: Your next Onam challenge may reward the same "
            "patience and timing that brought this pottu home."
        )


oracle = GeminiHoroscope()


# -------------------------
# Vision helpers
# -------------------------

def verified_direction(dx, dy, distance):
    """
    Exact movement decision.

    Gemini does NOT override this.
    """
    if distance < TARGET_TOLERANCE:
        return "STOP"

    # If horizontally aligned, correct vertical axis.
    if abs(dx) <= AXIS_TOLERANCE and abs(dy) > AXIS_TOLERANCE:
        return "DOWN" if dy > 0 else "UP"

    # If vertically aligned, correct horizontal axis.
    if abs(dy) <= AXIS_TOLERANCE and abs(dx) > AXIS_TOLERANCE:
        return "RIGHT" if dx > 0 else "LEFT"

    # Otherwise correct whichever axis is further away.
    if abs(dx) >= abs(dy):
        return "RIGHT" if dx > 0 else "LEFT"

    return "DOWN" if dy > 0 else "UP"


def detect_red_marker(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    lower_red_1 = np.array([0, 120, 70], dtype=np.uint8)
    upper_red_1 = np.array([10, 255, 255], dtype=np.uint8)

    lower_red_2 = np.array([170, 120, 70], dtype=np.uint8)
    upper_red_2 = np.array([180, 255, 255], dtype=np.uint8)

    mask_1 = cv2.inRange(hsv, lower_red_1, upper_red_1)
    mask_2 = cv2.inRange(hsv, lower_red_2, upper_red_2)

    red_mask = cv2.bitwise_or(mask_1, mask_2)

    kernel = np.ones((5, 5), dtype=np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(
        red_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    best_center = None
    best_area = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)

        if area < RED_MIN_AREA or area <= best_area:
            continue

        moments = cv2.moments(contour)

        if moments["m00"] == 0:
            continue

        center_x = int(moments["m10"] / moments["m00"])
        center_y = int(moments["m01"] / moments["m00"])

        best_center = (center_x, center_y)
        best_area = area

    return best_center


def add_hidden_path_sample(path_history, point):
    """
    Path is recorded internally but NEVER rendered on screen.
    """
    if not path_history:
        path_history.append(point)
        return

    last_x, last_y = path_history[-1]
    x, y = point

    if math.hypot(x - last_x, y - last_y) > PATH_SAMPLE_DISTANCE:
        path_history.append(point)


def calculate_path_length(path_history):
    length = 0.0

    for index in range(1, len(path_history)):
        x1, y1 = path_history[index - 1]
        x2, y2 = path_history[index]

        length += math.hypot(x2 - x1, y2 - y1)

    return length


def count_reversals(commands):
    opposites = {
        ("LEFT", "RIGHT"),
        ("RIGHT", "LEFT"),
        ("UP", "DOWN"),
        ("DOWN", "UP"),
    }

    reversals = 0

    for index in range(1, len(commands)):
        if (commands[index - 1], commands[index]) in opposites:
            reversals += 1

    return reversals


def wrap_text(text, max_chars=62):
    words = text.replace("\n", " ").split()

    lines = []
    current = []

    for word in words:
        candidate = " ".join(current + [word])

        if len(candidate) <= max_chars:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]

    if current:
        lines.append(" ".join(current))

    return lines


# -------------------------
# Result screen
# -------------------------

def make_result_frame(frozen_frame, final_error):
    canvas = frozen_frame.copy()
    height, width = canvas.shape[:2]

    panel_height = min(220, height)
    panel_top = height - panel_height

    overlay = canvas.copy()
    cv2.rectangle(
        overlay,
        (0, panel_top),
        (width, height),
        (0, 0, 0),
        -1,
    )

    cv2.addWeighted(
        overlay,
        0.74,
        canvas,
        0.26,
        0,
        canvas,
    )

    cv2.putText(
        canvas,
        "TARGET REACHED - CAMERA STOPPED",
        (20, panel_top + 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        canvas,
        f"Final error: {final_error}px",
        (20, panel_top + 66),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )

    cv2.putText(
        canvas,
        f"Gemini 3.7 Flash Oracle: {oracle.status}",
        (20, panel_top + 94),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    if oracle.result:
        y = panel_top + 126

        for line in wrap_text(oracle.result, max_chars=72)[:4]:
            cv2.putText(
                canvas,
                line,
                (20, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.47,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            y += 24
    else:
        cv2.putText(
            canvas,
            "Reading your Pottu destiny...",
            (20, panel_top + 132),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    cv2.putText(
        canvas,
        "Q / Esc = quit",
        (20, height - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.40,
        (180, 180, 180),
        1,
        cv2.LINE_AA,
    )

    return canvas


# -------------------------
# Main game
# -------------------------

def main():
    # Existing Raspberry Pi/OpenCV Haar cascade path from your current code.
    cascade_path = (
        "/usr/share/opencv4/haarcascades/"
        "haarcascade_frontalface_default.xml"
    )

    if not os.path.exists(cascade_path):
        raise FileNotFoundError(
            f"Haar cascade not found: {cascade_path}"
        )

    face_cascade = cv2.CascadeClassifier(cascade_path)

    if face_cascade.empty():
        raise RuntimeError("Could not load Haar face detector.")

    if sys.platform.startswith("linux"):
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera index {CAMERA_INDEX}"
        )

    # Hidden telemetry only. It is NOT drawn.
    path_history = []

    command_history = []
    command_counts = {
        "LEFT": 0,
        "RIGHT": 0,
        "UP": 0,
        "DOWN": 0,
        "STOP": 0,
    }

    last_counted_command = None

    round_started = time.monotonic()

    frozen_frame = None
    final_error = None

    try:
        while cap.isOpened():
            ok, frame = cap.read()

            if not ok:
                print("Camera frame unavailable.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(60, 60),
            )

            forehead = None

            # Use the largest face if multiple faces are visible.
            if len(faces) > 0:
                x, y, w, h = max(
                    faces,
                    key=lambda rect: rect[2] * rect[3],
                )

                forehead_x = int(x + (w / 2))
                forehead_y = int(y + (h * 0.30))

                forehead = (forehead_x, forehead_y)

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (255, 0, 0),
                    2,
                )

                # Show only target marker/ring.
                cv2.circle(
                    frame,
                    forehead,
                    TARGET_TOLERANCE,
                    (0, 255, 255),
                    2,
                )

                cv2.circle(
                    frame,
                    forehead,
                    4,
                    (0, 255, 255),
                    -1,
                )

            red_marker = detect_red_marker(frame)

            if red_marker:
                add_hidden_path_sample(
                    path_history,
                    red_marker,
                )

                # Current marker only.
                cv2.circle(
                    frame,
                    red_marker,
                    9,
                    (0, 0, 255),
                    -1,
                )

                cv2.circle(
                    frame,
                    red_marker,
                    11,
                    (255, 255, 255),
                    1,
                )

            # IMPORTANT:
            # There is intentionally NO cv2.polylines() and NO trace drawing.

            if forehead and red_marker:
                dx = forehead[0] - red_marker[0]
                dy = forehead[1] - red_marker[1]

                distance = int(
                    round(
                        math.hypot(
                            dx,
                            dy,
                        )
                    )
                )

                command = verified_direction(
                    dx,
                    dy,
                    distance,
                )

                # Record command changes for horoscope telemetry.
                if command != last_counted_command:
                    command_history.append(command)
                    command_counts[command] += 1
                    last_counted_command = command

                # Hardware receives verified local direction.
                esp32.send(command)

                # Gemini Live receives same verified direction and speaks Malayalam.
                live_guide.submit(
                    command,
                    dx,
                    dy,
                    distance,
                )

                display_color = (
                    (0, 255, 0)
                    if command == "STOP"
                    else (0, 255, 255)
                )

                cv2.putText(
                    frame,
                    f"COMMAND: {command}",
                    (20, 36),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.80,
                    display_color,
                    2,
                    cv2.LINE_AA,
                )

                cv2.putText(
                    frame,
                    f"Distance: {distance}px",
                    (20, 66),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

                print(
                    f"dx={dx:4d} "
                    f"dy={dy:4d} "
                    f"distance={distance:3d}px "
                    f"command={command}"
                )

                # ---------------------------
                # TARGET HIT
                # ---------------------------
                if command == "STOP":
                    final_error = distance

                    # Send STOP once more explicitly.
                    esp32.send("STOP")
                    live_guide.submit(
                        "STOP",
                        dx,
                        dy,
                        distance,
                    )

                    completion_time = (
                        time.monotonic()
                        - round_started
                    )

                    telemetry = {
                        "final_error": final_error,
                        "completion_time": completion_time,
                        "path_points": len(path_history),
                        "path_length": calculate_path_length(
                            path_history
                        ),
                        "reversals": count_reversals(
                            command_history
                        ),
                        "command_counts": dict(
                            command_counts
                        ),
                    }

                    # Freeze the exact winning frame.
                    frozen_frame = frame.copy()

                    # REQUIRED BEHAVIOUR:
                    # Stop camera immediately at target.
                    cap.release()

                    print("\nTARGET REACHED")
                    print(
                        f"Final error: "
                        f"{final_error}px"
                    )
                    print("Camera capture stopped.")
                    print(
                        f"Gemini horoscope model: "
                        f"{GEN_MODEL}\n"
                    )

                    # Gemini 3.7 Flash analyzes frozen image + telemetry.
                    oracle.start(
                        frozen_frame,
                        telemetry,
                    )

                    break

            else:
                missing = []

                if not forehead:
                    missing.append("FACE")

                if not red_marker:
                    missing.append("RED MARKER")

                cv2.putText(
                    frame,
                    "Waiting for: "
                    + " + ".join(missing),
                    (20, 36),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.67,
                    (0, 165, 255),
                    2,
                    cv2.LINE_AA,
                )

            cv2.putText(
                frame,
                f"Gemini Live: {live_guide.status}",
                (20, frame.shape[0] - 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.46,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            cv2.imshow(
                WINDOW_NAME,
                frame,
            )

            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), 27):
                return

        # ---------------------------
        # FINAL FROZEN HOROSCOPE VIEW
        # ---------------------------

        if frozen_frame is not None:
            while True:
                result_frame = make_result_frame(
                    frozen_frame,
                    final_error,
                )

                cv2.imshow(
                    WINDOW_NAME,
                    result_frame,
                )

                key = cv2.waitKey(30) & 0xFF

                if key in (ord("q"), 27):
                    break

    finally:
        if cap.isOpened():
            cap.release()

        esp32.send("STOP")
        live_guide.close()

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
