import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import cv2
import numpy as np
import serial

speech_cooldown = 2.5  # Seconds between spoken instructions

# Path/oracle settings
PATH_SAMPLE_DISTANCE = 5.0
TARGET_TOLERANCE = 30
GEMINI_MODEL = os.environ.get("POTTU_GEMINI_MODEL", "gemini-3.1-flash-live-preview")
GEMINI_ENABLED = os.environ.get("POTTU_GEMINI", "1").lower() not in {"0", "false", "off", "no"}


class BluetoothAudio:
    """Speak guidance through a connected Bluetooth headset without blocking video."""

    PROCESS_TIMEOUT = 3

    def __init__(self):
        self._state_lock = threading.Lock()
        self._is_speaking = False
        self._thread = None
        self._latest_text = None
        self._last_attempt_text = None
        self._last_attempt_time = 0
        self._last_delivered_text = None
        self._last_delivery_time = 0
        self._cancel_generation = 0

        self._espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._paplay = shutil.which("paplay")
        self._pactl = shutil.which("pactl")
        self._say = shutil.which("say")
        self._powershell = shutil.which("powershell") or shutil.which("pwsh")

        # Set this when more than one Bluetooth output is connected. Example:
        # POTTU_AUDIO_SINK=bluez_output.XX_XX_XX_XX_XX_XX.1 python model.py
        self._sink_override = os.environ.get("POTTU_AUDIO_SINK", "").strip() or None
        self._cached_sink = None
        self._last_sink_check = 0

        self._print_output_status()

    def _find_bluetooth_sink(self):
        """Return a connected Pulse/PipeWire Bluetooth sink name, if available."""
        if self._sink_override:
            return self._sink_override

        if not self._pactl:
            return None

        try:
            result = subprocess.run(
                [self._pactl, "list", "short", "sinks"],
                check=True,
                capture_output=True,
                text=True,
                timeout=0.75,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        bluetooth_sinks = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue

            sink_name = fields[1]
            if "bluez" in line.lower() or "bluetooth" in line.lower():
                bluetooth_sinks.append(sink_name)

        if not bluetooth_sinks:
            return None

        # Respect the user's selected default when it is one of the headsets.
        try:
            default_result = subprocess.run(
                [self._pactl, "get-default-sink"],
                check=True,
                capture_output=True,
                text=True,
                timeout=0.75,
            )
            default_sink = default_result.stdout.strip()
            if default_sink in bluetooth_sinks:
                return default_sink
        except (OSError, subprocess.SubprocessError):
            pass

        return bluetooth_sinks[0]

    def _get_bluetooth_sink(self, force_refresh=False):
        if self._sink_override:
            return self._sink_override

        now = time.monotonic()
        if force_refresh or now - self._last_sink_check >= 5:
            self._last_sink_check = now
            self._cached_sink = self._find_bluetooth_sink()

        return self._cached_sink

    def _print_output_status(self):
        if sys.platform.startswith("linux"):
            if not self._espeak:
                print("Audio disabled: install 'espeak-ng' or 'espeak'.")
                return

            sink = self._get_bluetooth_sink(force_refresh=True)
            if self._sink_override and self._paplay:
                print(f"Audio output: configured sink ({self._sink_override})")
            elif sink and self._paplay:
                print(f"Audio output: Bluetooth headset ({sink})")
            elif self._paplay:
                print("Audio output: system default (no Bluetooth sink detected yet)")
            else:
                print("Audio output: system default (install 'pulseaudio-utils' for direct Bluetooth routing)")
        elif sys.platform == "darwin" and self._say:
            print("Audio output: macOS system default")
        elif sys.platform == "win32" and self._powershell:
            print("Audio output: Windows system default")
        else:
            print("Audio disabled: no supported speech command was found.")

    def request(self, text, cooldown):
        """Speak changed guidance immediately and repeat it after the cooldown."""
        now = time.monotonic()

        with self._state_lock:
            # A busy worker uses this value to avoid retrying an obsolete direction.
            self._latest_text = text
            if self._is_speaking:
                return False

            if (
                text == self._last_delivered_text
                and now - self._last_delivery_time <= cooldown
            ):
                return False

            if (
                text == self._last_attempt_text
                and now - self._last_attempt_time <= cooldown
            ):
                return False

            self._is_speaking = True
            self._last_attempt_text = text
            self._last_attempt_time = now
            generation = self._cancel_generation

        thread = threading.Thread(
            target=self._speak,
            args=(text, generation),
            daemon=True,
        )
        self._thread = thread

        try:
            thread.start()
        except RuntimeError:
            with self._state_lock:
                self._is_speaking = False
                self._last_attempt_text = None
            raise

        return True

    def cancel(self):
        """Prevent an in-flight failure from retrying stale guidance."""
        with self._state_lock:
            self._cancel_generation += 1
            self._latest_text = None
            self._last_attempt_text = None
            self._last_delivered_text = None
            self._last_delivery_time = 0

    def _is_current(self, text, generation):
        with self._state_lock:
            return (
                self._latest_text == text
                and self._cancel_generation == generation
            )

    def _speak(self, text, generation):
        outcome = False

        try:
            if sys.platform.startswith("linux"):
                outcome = self._speak_linux(text, generation)
            elif sys.platform == "darwin" and self._say:
                if not self._is_current(text, generation):
                    outcome = None
                else:
                    subprocess.run(
                        [self._say, "-r", "170", text],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=self.PROCESS_TIMEOUT,
                    )
                    outcome = True
            elif sys.platform == "win32" and self._powershell:
                if not self._is_current(text, generation):
                    outcome = None
                else:
                    script = (
                        "Add-Type -AssemblyName System.Speech; "
                        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$voice.Speak($args[0])"
                    )
                    subprocess.run(
                        [self._powershell, "-NoProfile", "-Command", script, text],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=self.PROCESS_TIMEOUT,
                    )
                    outcome = True
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Audio playback error: {error}")
        finally:
            with self._state_lock:
                if outcome is True and generation == self._cancel_generation:
                    self._last_delivered_text = text
                    self._last_delivery_time = time.monotonic()
                elif outcome is None and self._last_attempt_text == text:
                    # Cancellation should be eligible for an immediate fresh attempt.
                    self._last_attempt_text = None

                self._is_speaking = False

    def _speak_linux(self, text, generation):
        if not self._espeak:
            return False

        # paplay sends a new audio stream straight to the selected Bluetooth sink.
        # eSpeak's direct playback remains a fallback when Pulse/PipeWire tools are absent.
        if not self._paplay:
            if not self._is_current(text, generation):
                return None

            self._speak_espeak_default(text)
            return True

        descriptor, wav_path = tempfile.mkstemp(prefix="pottu_", suffix=".wav")
        os.close(descriptor)

        try:
            subprocess.run(
                [self._espeak, "-s", "170", "-w", wav_path, text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.PROCESS_TIMEOUT,
            )

            if not self._is_current(text, generation):
                return None

            sink = self._get_bluetooth_sink()
            if not self._is_current(text, generation):
                return None

            try:
                self._play_wav(wav_path, sink or "@DEFAULT_SINK@")
                return True
            except (OSError, subprocess.SubprocessError) as error:
                self._last_sink_check = 0
                print(f"Selected audio output failed: {error}")

                if not self._is_current(text, generation):
                    return None

                # A configured or recently disconnected sink may be stale.
                # Retry once through the live system default before ALSA fallback.
                if sink:
                    try:
                        self._play_wav(wav_path, "@DEFAULT_SINK@")
                        return True
                    except (OSError, subprocess.SubprocessError) as default_error:
                        error = default_error

                if not self._is_current(text, generation):
                    return None

                print(
                    "Pulse/PipeWire playback failed; using system output: "
                    f"{error}"
                )
                self._speak_espeak_default(text)
                return True
        finally:
            try:
                os.remove(wav_path)
            except FileNotFoundError:
                pass

    def _play_wav(self, wav_path, sink):
        subprocess.run(
            [
                self._paplay,
                "--client-name=Pottu Guidance",
                "--stream-name=Direction",
                f"--device={sink}",
                wav_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=self.PROCESS_TIMEOUT,
        )

    def _speak_espeak_default(self, text):
        subprocess.run(
            [self._espeak, "-s", "170", text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=self.PROCESS_TIMEOUT,
        )

    def close(self):
        """Allow a short instruction already in progress to finish on shutdown."""
        self.cancel()

        with self._state_lock:
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=self.PROCESS_TIMEOUT + 0.5)


audio = BluetoothAudio()
atexit.register(audio.close)


class GeminiOracle:
    """Analyze a completed round asynchronously using one annotated OpenCV snapshot."""

    def __init__(self):
        self.enabled = GEMINI_ENABLED
        self.model = GEMINI_MODEL
        self._lock = threading.Lock()
        self._busy = False
        self._result = None
        self._status = "READY" if self.enabled else "OFFLINE"

    @property
    def status(self):
        with self._lock:
            return self._status

    @property
    def result(self):
        with self._lock:
            return self._result

    def reset(self):
        with self._lock:
            self._result = None
            self._status = "READY" if self.enabled else "OFFLINE"

    def consult(self, frame, final_error, path_points):
        """Start one non-blocking multimodal analysis request for the completed round."""
        if not self.enabled:
            return False

        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._status = "THINKING"

        snapshot = frame.copy()
        thread = threading.Thread(
            target=self._worker,
            args=(snapshot, final_error, list(path_points)),
            daemon=True,
        )
        thread.start()
        return True

    def _worker(self, frame, final_error, path_points):
        try:
            # Lazy import: the game still works when google-genai is not installed.
            from google import genai
            from google.genai import types

            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
            )
            if not ok:
                raise RuntimeError("Could not encode oracle snapshot")

            prompt = f"""
You are the mischievous PottuAI Oracle for the Onam game Sundarikk Pottu Thodal.
Analyze this completed-round image only.

Visual grounding:
- The YELLOW circle marks the intended forehead target.
- The RED dot is the tracked pen/marker position.
- The glowing WHITE line is the player's recorded movement path.
- Final measured pixel error from the local controller: {final_error}px.
- Sampled path points: {len(path_points)}.

Give ONE short playful horoscope-style verdict, maximum 18 words.
Mention precision or movement style. Do not invent measurements beyond the supplied error.
""".strip()

            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            image_part = types.Part.from_bytes(
                data=encoded.tobytes(), mime_type="image/jpeg"
            )
            response = client.models.generate_content(
                model=self.model,
                contents=[image_part, prompt],
            )
            text = (response.text or "").strip()
            if not text:
                text = f"The oracle sees a finish just {final_error} pixels from destiny."

            with self._lock:
                self._result = text
                self._status = "DONE"

            print(f"GEMINI ORACLE: {text}")
            # Navigation has ended, so the oracle may use the same audio fallback.
            audio.request(text, 0)
        except Exception as error:
            print(f"GEMINI: OFFLINE ({error})")
            with self._lock:
                self._result = None
                self._status = "OFFLINE"
        finally:
            with self._lock:
                self._busy = False


oracle = GeminiOracle()

# Initialize Serial Connection to ESP32
# Replace 'COM3' with your actual ESP32 COM port
try:
    ser = None
    #ser = serial.Serial('COM3', 115200, timeout=1)
    time.sleep(2)  # Give ESP32 time to reset after opening connection
    print("Serial connected to ESP32!")
except Exception as e:
    print(f"Serial connection error: {e}")
    ser = None

xml_filename = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(xml_filename)

if face_cascade.empty():
    raise IOError(f"Could not load Haar Cascade XML from file: {xml_filename}")

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)

path_history = []
round_complete = False
final_error = None

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        print("Unable to fetch webcam frame.")
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )

    forehead_x = None
    forehead_y = None

    for x, y, w, h in faces:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)

        # Calculate forehead coordinates (centered horizontally, ~30% from top of face box)
        forehead_x = int(x + (w / 2))
        forehead_y = int(y + (h * 0.3))

        # Oracle-grounded target visualization: yellow ring on the forehead.
        cv2.circle(frame, (forehead_x, forehead_y), TARGET_TOLERANCE, (0, 255, 255), 2)
        cv2.circle(frame, (forehead_x, forehead_y), 4, (0, 255, 255), -1)

        # Calculate face center target coordinates
        center_x = float(x + (w // 2))
        center_y = float(y + (h // 2))

        # Print face coordinates to terminal
        print(f"Face Coordinates: X = {center_x:.1f}, Y = {center_y:.1f}")

        # Send space-separated "X Y\n" format expected by parseFloat()
        if ser and ser.is_open:
            data_string = f"{center_x:.1f} {center_y:.1f}\n"
            ser.write(data_string.encode('utf-8'))

    # Detect red object (marker cap) using HSV color thresholding
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Lower and upper range for red color in HSV space
    lower_red1 = np.array([0, 120, 70])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 120, 70])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv_frame, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv_frame, lower_red2, upper_red2)
    red_mask = mask1 + mask2

    # Find contours of the red object
    contours, _ = cv2.findContours(red_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    red_center_x = None
    red_center_y = None

    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 500:  # Minimum area threshold to ignore noise
            M = cv2.moments(contour)
            if M["m00"] != 0:
                red_center_x = int(M["m10"] / M["m00"])
                red_center_y = int(M["m01"] / M["m00"])

                # Draw the tracked marker as a red dot for the oracle snapshot.
                cv2.circle(frame, (red_center_x, red_center_y), 9, (0, 0, 255), -1)
                cv2.circle(frame, (red_center_x, red_center_y), 11, (255, 255, 255), 1)

                # Distance-based path sampling: only store a point after >5 px movement.
                current_point = (red_center_x, red_center_y)
                if not path_history:
                    path_history.append(current_point)
                else:
                    last_x, last_y = path_history[-1]
                    sample_distance = np.hypot(red_center_x - last_x, red_center_y - last_y)
                    if sample_distance > PATH_SAMPLE_DISTANCE:
                        path_history.append(current_point)

                print(f"Red Object Coordinates: X = {red_center_x}, Y = {red_center_y}")
                break

    # Render a glowing white trajectory from sampled path points.
    if len(path_history) >= 2:
        trail = np.array(path_history, dtype=np.int32).reshape((-1, 1, 2))
        # Wide dim stroke + thin bright stroke approximates a glow in OpenCV.
        cv2.polylines(frame, [trail], False, (130, 130, 130), 8, cv2.LINE_AA)
        cv2.polylines(frame, [trail], False, (255, 255, 255), 3, cv2.LINE_AA)

    # Calculate difference between tracked marker and forehead target to guide user
    if forehead_x is not None and red_center_x is not None:
        dx = forehead_x - red_center_x
        dy = forehead_y - red_center_y
        distance = int(np.sqrt(dx**2 + dy**2))

        # Draw a line between the two points
        cv2.line(frame, (red_center_x, red_center_y), (forehead_x, forehead_y), (255, 255, 0), 2)

        # Build movement directions
        directions = []
        if abs(dx) > 20:
            directions.append("RIGHT" if dx > 0 else "LEFT")
        if abs(dy) > 20:
            directions.append("DOWN" if dy > 0 else "UP")

        if distance < TARGET_TOLERANCE:
            guide_text = "TARGET REACHED!"
            speech_text = "Target reached"
            color = (0, 255, 0)
            final_error = distance
        else:
            guide_text = f"Move Hand: {' + '.join(directions)}"
            speech_text = " and ".join(direction.lower() for direction in directions)
            color = (0, 255, 255)

        cv2.putText(frame, guide_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        print(f"Distance: {distance}px | Guidance: {guide_text}")

        if not round_complete:
            audio.request(speech_text, speech_cooldown)

        # Trigger Gemini exactly once after the locally-computed win condition.
        if distance < TARGET_TOLERANCE and not round_complete:
            round_complete = True
            audio.cancel()
            audio.request("Target reached", 0)
            oracle.consult(frame, final_error, path_history)
    else:
        # Announce the current direction immediately after tracking is reacquired.
        audio.cancel()

    # Oracle overlay is presentation-only; gameplay never waits for Gemini.
    oracle_status = oracle.status
    cv2.putText(
        frame,
        f"GEMINI ORACLE: {oracle_status}",
        (20, frame.shape[0] - 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    oracle_text = oracle.result
    if oracle_text:
        # Keep the overlay compact; full text is also printed in the terminal.
        display_text = oracle_text[:90] + ("..." if len(oracle_text) > 90 else "")
        cv2.putText(
            frame,
            display_text,
            (20, frame.shape[0] - 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )

    cv2.imshow("PottuAI - Path + Gemini Oracle", frame)
    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27):
        break
    if key == ord("r"):
        path_history.clear()
        round_complete = False
        final_error = None
        oracle.reset()
        audio.cancel()
        print("Round reset.")

cap.release()
cv2.destroyAllWindows()
audio.close()
if ser and ser.is_open:
    ser.close()
