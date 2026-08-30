import atexit
import asyncio
import base64
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import serial

from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ============================================================
# PottuAI — Raspberry Pi Oracle Edition
#
# GAMEPLAY:
#   OpenCV + deterministic geometry
#
# LIVE MALAYALAM VOICE:
#   gemini-3.1-flash-live-preview
#
# FINAL HOROSCOPE:
#   gemini-3.7-flash
#
# RESULT UI:
#   PySide6 / Qt
#
# IMPORTANT:
# - NO Malayalam text is rendered with cv2.putText().
# - NO trajectory is shown during gameplay.
# - The path is stored internally and drawn only into a PRIVATE
#   diagnostic image sent to Gemini.
# - When target is reached:
#       cap.release()
#       cv2.destroyAllWindows()
#   happens BEFORE the Oracle result UI opens.
#
# INSTALL:
#   pip install -U google-genai opencv-python numpy pyserial PySide6
#
# Raspberry Pi:
#   sudo apt install pulseaudio-utils alsa-utils fonts-noto-core fonts-noto-extra
#
# Environment:
#   export GEMINI_API_KEY="..."
#
# Optional:
#   export POTTU_CAMERA=0
#   export POTTU_SERIAL_PORT=/dev/ttyUSB0
#   export POTTU_TARGET_TOLERANCE=30
#   export POTTU_AUDIO_SINK=bluez_output.XX_XX_XX_XX_XX_XX.1
# ============================================================


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

LIVE_MODEL = "gemini-3.1-flash-live-preview"
ORACLE_MODEL = "gemma4:e2b"
OLLAMA_BASE_URL = os.environ.get("POTTU_OLLAMA_URL", "http://192.168.11.157:11434").rstrip("/")

CAMERA_INDEX = int(os.environ.get("POTTU_CAMERA", "0"))

TARGET_TOLERANCE = int(
    os.environ.get("POTTU_TARGET_TOLERANCE", "30")
)

AXIS_TOLERANCE = int(
    os.environ.get("POTTU_AXIS_TOLERANCE", "20")
)

GUIDANCE_REPEAT_COOLDOWN = float(
    os.environ.get("POTTU_GUIDANCE_COOLDOWN", "1.3")
)

RED_MIN_AREA = int(
    os.environ.get("POTTU_RED_MIN_AREA", "500")
)

PATH_SAMPLE_DISTANCE = 5.0

SERIAL_PORT = os.environ.get(
    "POTTU_SERIAL_PORT",
    "",
).strip()

SERIAL_BAUD = 115200

WINDOW_NAME = "PottuAI"

HISTORY_FILE = Path(
    os.environ.get(
        "POTTU_HISTORY_FILE",
        "pottuai_history.json",
    )
)

MAX_HISTORY = 6

MALAYALAM_COMMANDS = {
    "LEFT": "ഇടത്തോട്ട്",
    "RIGHT": "വലത്തോട്ട്",
    "UP": "മുകളിലേക്ക്",
    "DOWN": "താഴേക്ക്",
    "STOP": "നിർത്തൂ",
}

ORACLE_PERSONAS = [
    (
        "മഹാബലിയുടെ രാജകൊട്ടാരത്തിലെ പരിഹാസപ്രിയനായ "
        "ഓണം ജ്യോത്സ്യൻ"
    ),
    (
        "പൂക്കളത്തിന്റെ വളവുകളിൽ വിധി വായിക്കുന്ന "
        "രഹസ്യ ഓറക്കിൾ"
    ),
    (
        "പാതാളത്തിൽ നിന്ന് കളിക്കാരുടെ കൈയാത്ര "
        "നിരീക്ഷിക്കുന്ന മഹാബലിയുടെ ദർശകൻ"
    ),
    (
        "വള്ളംകളിയുടെ ആവേശത്തിൽ ഓരോ വളവും "
        "നാടകീയമായി വായിക്കുന്ന ഉത്സവ പ്രവാചകൻ"
    ),
    (
        "ചിരിപ്പിച്ചുകൊണ്ട് സത്യം പറയുന്ന "
        "പഴയകാല ഓണം ദൈവജ്ഞൻ"
    ),
    (
        "കൈയുടെ zig-zag വഴികളിൽ നിന്ന് സ്വഭാവം "
        "വായിക്കുന്ന രസികൻ ഓറക്കിൾ"
    ),
]


# ------------------------------------------------------------
# Malayalam font setup
# ------------------------------------------------------------

def install_app_font():
    """
    Qt can render Malayalam correctly when a Malayalam font is installed.
    Try common Noto locations explicitly, then use system font fallback.
    """

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansMalayalam-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMalayalamUI-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansMalayalamUI-Regular.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            font_id = QFontDatabase.addApplicationFont(path)

            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(
                    font_id
                )

                if families:
                    print(
                        f"Malayalam UI font: "
                        f"{families[0]}"
                    )

                    return families[0]

    # Qt/fontconfig fallback.
    print(
        "Malayalam font file not explicitly found; "
        "using Qt system fallback."
    )

    return "Noto Sans Malayalam"


MALAYALAM_FONT_FAMILY = None


# ------------------------------------------------------------
# PCM audio playback
# ------------------------------------------------------------

class PCMPlayer:
    """
    Interruptible low-latency PCM player.

    Gemini Live audio output is:
      - mono
      - signed 16-bit little-endian
      - 24 kHz

    request() immediately replaces the current direction audio.
    This is important for navigation because stale speech is worse
    than missing speech.
    """

    def __init__(self):
        self.paplay = shutil.which("paplay")
        self.aplay = shutil.which("aplay")
        self.pactl = shutil.which("pactl")

        self.sink_override = (
            os.environ.get(
                "POTTU_AUDIO_SINK",
                "",
            ).strip()
            or None
        )

        self.cached_sink = None
        self.last_sink_check = 0.0

        self.lock = threading.Lock()
        self.current_process = None
        self.play_generation = 0

        self._print_status()

    def _find_bluetooth_sink(self):
        if self.sink_override:
            return self.sink_override

        if not self.pactl:
            return None

        try:
            result = subprocess.run(
                [
                    self.pactl,
                    "list",
                    "short",
                    "sinks",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=1.0,
            )
        except (
            OSError,
            subprocess.SubprocessError,
        ):
            return None

        bluetooth = []

        for line in result.stdout.splitlines():
            fields = line.split()

            if len(fields) < 2:
                continue

            lower = line.lower()

            if (
                "bluez" in lower
                or "bluetooth" in lower
            ):
                bluetooth.append(
                    fields[1]
                )

        if not bluetooth:
            return None

        try:
            default_sink = subprocess.run(
                [
                    self.pactl,
                    "get-default-sink",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=1.0,
            ).stdout.strip()

            if default_sink in bluetooth:
                return default_sink

        except (
            OSError,
            subprocess.SubprocessError,
        ):
            pass

        return bluetooth[0]

    def get_sink(self, force=False):
        now = time.monotonic()

        if (
            force
            or now - self.last_sink_check > 5
        ):
            self.last_sink_check = now
            self.cached_sink = (
                self._find_bluetooth_sink()
            )

        return self.cached_sink

    def _print_status(self):
        sink = self.get_sink(
            force=True
        )

        if self.paplay:
            if sink:
                print(
                    "Audio output: "
                    f"Bluetooth -> {sink}"
                )
            else:
                print(
                    "Audio output: "
                    "Pulse/PipeWire default"
                )

        elif self.aplay:
            print(
                "Audio output: ALSA default"
            )

        else:
            print(
                "WARNING: install "
                "pulseaudio-utils or alsa-utils "
                "for Gemini audio playback."
            )

    def stop(self):
        with self.lock:
            self.play_generation += 1
            process = self.current_process
            self.current_process = None

        if process:
            try:
                process.terminate()
                process.wait(timeout=0.15)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def request(
        self,
        pcm_bytes,
        interrupt=True,
    ):
        """
        Play PCM asynchronously.

        interrupt=True:
          stop current clip and immediately play newest direction.

        interrupt=False:
          used for the long Oracle readout.
        """
        if not pcm_bytes:
            return

        if interrupt:
            self.stop()

        with self.lock:
            self.play_generation += 1
            generation = self.play_generation

        threading.Thread(
            target=self._play_worker,
            args=(
                pcm_bytes,
                generation,
            ),
            daemon=True,
        ).start()

    def _play_worker(
        self,
        pcm_bytes,
        generation,
    ):
        sink = self.get_sink()
        process = None

        try:
            if self.paplay:
                command = [
                    self.paplay,
                    "--raw",
                    "--rate=24000",
                    "--channels=1",
                    "--format=s16le",
                    "--client-name=PottuAI",
                    "--stream-name=Gemini Malayalam",
                ]

                if sink:
                    command.append(
                        f"--device={sink}"
                    )

                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            elif self.aplay:
                process = subprocess.Popen(
                    [
                        self.aplay,
                        "-q",
                        "-t",
                        "raw",
                        "-f",
                        "S16_LE",
                        "-c",
                        "1",
                        "-r",
                        "24000",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            else:
                return

            with self.lock:
                if generation != self.play_generation:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    return

                self.current_process = process

            try:
                process.stdin.write(
                    pcm_bytes
                )
                process.stdin.close()
                process.wait(timeout=30)

            except (
                BrokenPipeError,
                OSError,
                subprocess.SubprocessError,
            ):
                pass

        finally:
            with self.lock:
                if (
                    self.current_process
                    is process
                ):
                    self.current_process = None


pcm_player = PCMPlayer()


# ------------------------------------------------------------
# ESP32
# ------------------------------------------------------------

class ESP32Link:
    def __init__(self):
        self.ser = None
        self.last_command = None

        if not SERIAL_PORT:
            print(
                "ESP32: disabled "
                "(set POTTU_SERIAL_PORT)"
            )
            return

        try:
            self.ser = serial.Serial(
                SERIAL_PORT,
                SERIAL_BAUD,
                timeout=0.1,
            )

            time.sleep(2)

            print(
                f"ESP32: connected "
                f"{SERIAL_PORT}"
            )

        except Exception as exc:
            print(
                f"ESP32: offline ({exc})"
            )

            self.ser = None

    def send(self, command):
        if command == self.last_command:
            return

        self.last_command = command

        if (
            not self.ser
            or not self.ser.is_open
        ):
            return

        try:
            self.ser.write(
                (
                    command
                    + "\n"
                ).encode("utf-8")
            )

        except Exception as exc:
            print(
                f"ESP32 write failed: {exc}"
            )

            try:
                self.ser.close()
            except Exception:
                pass

            self.ser = None

    def reset(self):
        self.last_command = None

    def close(self):
        try:
            if (
                self.ser
                and self.ser.is_open
            ):
                self.ser.write(
                    b"STOP\n"
                )

                self.ser.close()

        except Exception:
            pass


esp32 = ESP32Link()
atexit.register(
    esp32.close
)


# ------------------------------------------------------------
# Fast Gemini Malayalam voice
# ------------------------------------------------------------

class GeminiLiveVoice:
    """
    Low-latency Gemini Malayalam audio.

    Why this version is faster:
    ---------------------------
    Calling Gemini Live for every direction adds network + model latency.

    Instead:
      1. On startup, Gemini Live generates LEFT/RIGHT/UP/DOWN/STOP
         in Malayalam once.
      2. The raw 24 kHz PCM clips are cached on disk.
      3. Gameplay plays those Gemini-generated clips locally and instantly.
      4. STOP interrupts any stale direction immediately.

    The final Oracle is different:
      - it is unique every round,
      - so it gets its own dedicated Gemini Live request,
      - not the direction queue.

    This keeps the direction voice Gemini-generated while removing
    real-time network latency from the control loop.
    """

    CACHE_VERSION = "v2"
    CACHE_DIR = Path(
        os.environ.get(
            "POTTU_GEMINI_AUDIO_CACHE",
            ".pottuai_gemini_audio",
        )
    )

    def __init__(self):
        self.api_key = os.environ.get(
            "GEMINI_API_KEY",
            "",
        ).strip()

        self.enabled = bool(
            self.api_key
        )

        self.status = (
            "PREPARING"
            if self.enabled
            else "OFFLINE"
        )

        self.command_audio = {}
        self.cache_ready = threading.Event()
        self.close_event = threading.Event()

        self.last_command = None
        self.last_command_time = 0.0

        self.prewarm_thread = None

        self.CACHE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.enabled:
            self.prewarm_thread = threading.Thread(
                target=self._prewarm_thread,
                daemon=True,
            )
            self.prewarm_thread.start()

        else:
            print(
                "Gemini Live: "
                "GEMINI_API_KEY missing"
            )

    def reset_round(self):
        self.last_command = None
        self.last_command_time = 0.0

    def wait_until_ready(
        self,
        timeout=25,
    ):
        if not self.enabled:
            return False

        ready = self.cache_ready.wait(
            timeout=timeout
        )

        if not ready:
            print(
                "Gemini voice cache is still preparing; "
                "game will continue."
            )

        return ready

    def _cache_path(
        self,
        command,
    ):
        return (
            self.CACHE_DIR
            / (
                f"{self.CACHE_VERSION}_"
                f"{LIVE_MODEL}_"
                f"{command}.pcm"
            )
        )

    def _load_cache(self):
        loaded = {}

        for command in MALAYALAM_COMMANDS:
            path = self._cache_path(
                command
            )

            if (
                path.exists()
                and path.stat().st_size > 1000
            ):
                loaded[command] = (
                    path.read_bytes()
                )

        self.command_audio.update(
            loaded
        )

        return (
            len(loaded)
            == len(MALAYALAM_COMMANDS)
        )

    def _prewarm_thread(self):
        try:
            if self._load_cache():
                self.status = "READY"
                self.cache_ready.set()

                print(
                    "Gemini Malayalam direction "
                    "audio cache: READY"
                )
                return

            print(
                "Preparing Gemini Malayalam "
                "direction audio cache..."
            )

            asyncio.run(
                self._build_missing_cache()
            )

            self._load_cache()

            if (
                len(self.command_audio)
                == len(MALAYALAM_COMMANDS)
            ):
                self.status = "READY"

                print(
                    "Gemini Malayalam direction "
                    "audio cache: READY"
                )
            else:
                self.status = "PARTIAL"

                print(
                    "Gemini direction cache: "
                    "PARTIAL"
                )

        except Exception as exc:
            self.status = "OFFLINE"

            print(
                "Gemini voice cache failed: "
                f"{exc}"
            )

        finally:
            self.cache_ready.set()

    async def _build_missing_cache(self):
        for command, malayalam in (
            MALAYALAM_COMMANDS.items()
        ):
            path = self._cache_path(
                command
            )

            if (
                path.exists()
                and path.stat().st_size > 1000
            ):
                continue

            pcm = await self._live_audio_request(
                (
                    "ഈ വാക്ക് മാത്രം വ്യക്തമായി പറയുക. "
                    "മറ്റൊന്നും പറയരുത്:\n"
                    f"{malayalam}"
                ),
                mode="GUIDE",
            )

            if pcm:
                path.write_bytes(
                    pcm
                )

                self.command_audio[
                    command
                ] = pcm

                print(
                    "Cached Gemini command: "
                    f"{command} -> {malayalam}"
                )

    async def _live_audio_request(
        self,
        text,
        mode,
    ):
        from google import genai

        client = genai.Client(
            api_key=self.api_key
        )

        if mode == "GUIDE":
            system_instruction = """
നിങ്ങൾ PottuAIയുടെ മലയാളം navigation voice ആണ്.
User നൽകുന്ന മലയാളം direction phrase മാത്രം പറയുക.
മറ്റൊരു വാക്കും, greetingഉം, explanationഉം ചേർക്കരുത്.
വേഗത്തിലും വ്യക്തമായും പറയുക.
""".strip()

        else:
            system_instruction = """
നിങ്ങൾ PottuAIയുടെ മലയാളം ഓറക്കിൾ ശബ്ദമാണ്.
User നൽകുന്ന മലയാളം horoscope paragraph മുഴുവനായും
അർത്ഥം മാറ്റാതെ വായിക്കുക.
വാചകങ്ങൾ ഒഴിവാക്കരുത്.
പുതിയ വാചകങ്ങൾ ചേർക്കരുത്.
വളരെ മന്ദഗതിയല്ലാതെ, വ്യക്തവും നാടകീയവുമായ സ്വാഭാവിക
Malayalam delivery ഉപയോഗിക്കുക.
""".strip()

        config = {
            "response_modalities": [
                "AUDIO"
            ],
            "system_instruction": (
                system_instruction
            ),
        }

        audio_chunks = []

        async with client.aio.live.connect(
            model=LIVE_MODEL,
            config=config,
        ) as session:

            await session.send_realtime_input(
                text=text
            )

            async for response in (
                session.receive()
            ):
                content = (
                    response.server_content
                )

                if not content:
                    continue

                model_turn = (
                    content.model_turn
                )

                if (
                    model_turn
                    and model_turn.parts
                ):
                    for part in (
                        model_turn.parts
                    ):
                        inline = (
                            part.inline_data
                        )

                        if (
                            inline
                            and inline.data
                        ):
                            audio_chunks.append(
                                inline.data
                            )

        return b"".join(
            audio_chunks
        )

    def guide(
        self,
        command,
        dx,
        dy,
        distance,
    ):
        """
        Instant local playback of a Gemini-generated cached command.
        dx/dy are kept in signature so existing game code stays unchanged.
        """

        if command not in MALAYALAM_COMMANDS:
            return

        now = time.monotonic()

        changed = (
            command
            != self.last_command
        )

        repeat_due = (
            now
            - self.last_command_time
            >= GUIDANCE_REPEAT_COOLDOWN
        )

        if (
            not changed
            and not repeat_due
        ):
            return

        self.last_command = command
        self.last_command_time = now

        pcm = self.command_audio.get(
            command
        )

        if not pcm:
            path = self._cache_path(
                command
            )

            if path.exists():
                try:
                    pcm = path.read_bytes()
                    self.command_audio[
                        command
                    ] = pcm
                except OSError:
                    pcm = None

        if pcm:
            # Every new direction replaces stale speech.
            pcm_player.request(
                pcm,
                interrupt=True,
            )

        else:
            print(
                "Gemini direction audio "
                f"not ready: {command}"
            )

    def speak_oracle(
        self,
        text,
    ):
        """
        Dedicated one-shot Oracle reader.

        It does NOT share a queue with direction guidance, so the final
        horoscope cannot be dropped behind STOP or another direction.
        """

        if (
            not self.enabled
            or not text
        ):
            return

        # Stop any short direction clip before starting the final narration.
        pcm_player.stop()

        threading.Thread(
            target=self._oracle_speech_thread,
            args=(text,),
            daemon=True,
        ).start()

    def _oracle_speech_thread(
        self,
        text,
    ):
        try:
            self.status = (
                "ORACLE SPEAKING"
            )

            pcm = asyncio.run(
                self._live_audio_request(
                    (
                        "താഴെയുള്ള മലയാളം horoscope "
                        "മുഴുവനായി വായിക്കുക:\n\n"
                        f"{text}"
                    ),
                    mode="ORACLE",
                )
            )

            if not pcm:
                raise RuntimeError(
                    "Gemini Live returned "
                    "no Oracle audio"
                )

            pcm_player.request(
                pcm,
                interrupt=False,
            )

            print(
                "Oracle Malayalam audio: "
                "PLAYING"
            )

        except Exception as exc:
            print(
                "Oracle Malayalam TTS failed: "
                f"{exc}"
            )

        finally:
            self.status = "READY"

    def close(self):
        self.close_event.set()
        pcm_player.stop()


live_voice = GeminiLiveVoice()
atexit.register(
    live_voice.close
)


# ------------------------------------------------------------
# History
# ------------------------------------------------------------

def load_history():
    try:
        with HISTORY_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(
                file
            )

        if isinstance(
            data,
            list,
        ):
            return data[
                -MAX_HISTORY:
            ]

    except (
        OSError,
        json.JSONDecodeError,
    ):
        pass

    return []


def save_history(
    telemetry,
    horoscope,
    persona,
):
    history = load_history()

    history.append(
        {
            "timestamp": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "telemetry": telemetry,
            "horoscope": horoscope,
            "persona": persona,
        }
    )

    history = history[
        -MAX_HISTORY:
    ]

    try:
        with HISTORY_FILE.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                history,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except OSError as exc:
        print(
            "Could not save history: "
            f"{exc}"
        )


# ------------------------------------------------------------
# Local Gemma 4 Oracle through Ollama
# ------------------------------------------------------------

class GeminiOracle:
    """
    Final Oracle analysis is generated by the LOCAL/LAN Gemma model:

        Ollama:  http://192.168.11.157:11434
        Model:   gemma4:e2b

    The class name is intentionally kept as GeminiOracle so the existing
    Qt/UI code does not need invasive changes.

    Analysis flow:
      1. Build a PRIVATE hidden-path image.
      2. Send clean final frame + hidden-path image + telemetry to Ollama.
      3. If the local model/server rejects image input, automatically retry
         as a telemetry-only request instead of breaking the demo.
      4. Display the Malayalam Oracle text in Qt.
      5. Gemini Live still reads that Malayalam text aloud.
    """

    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.model = ORACLE_MODEL
        self.lock = threading.Lock()

        self.busy = False
        self.status = "READY"
        self.text = None
        self.error = None
        self.persona = None

    def reset(self):
        with self.lock:
            self.busy = False
            self.status = "READY"
            self.text = None
            self.error = None
            self.persona = None

    @staticmethod
    def build_private_path_image(
        clean_frame,
        path_history,
        target,
    ):
        """
        PRIVATE ONLY. Never shown in the result UI.

        Gemma sees:
        - darkened camera frame
        - glowing white trajectory
        - cyan start
        - red final point
        - yellow target
        """
        diagnostic = clean_frame.copy()
        black = np.zeros_like(diagnostic)

        cv2.addWeighted(
            diagnostic,
            0.18,
            black,
            0.82,
            0,
            diagnostic,
        )

        if len(path_history) >= 2:
            path = np.array(
                path_history,
                dtype=np.int32,
            ).reshape((-1, 1, 2))

            cv2.polylines(
                diagnostic,
                [path],
                False,
                (90, 90, 90),
                11,
                cv2.LINE_AA,
            )

            cv2.polylines(
                diagnostic,
                [path],
                False,
                (255, 255, 255),
                4,
                cv2.LINE_AA,
            )

        if path_history:
            start = tuple(map(int, path_history[0]))
            end = tuple(map(int, path_history[-1]))

            cv2.circle(
                diagnostic,
                start,
                9,
                (255, 255, 0),
                -1,
            )

            cv2.circle(
                diagnostic,
                end,
                9,
                (0, 0, 255),
                -1,
            )

        if target:
            cv2.circle(
                diagnostic,
                tuple(map(int, target)),
                TARGET_TOLERANCE,
                (0, 255, 255),
                3,
            )

        return diagnostic

    def start(
        self,
        clean_frame,
        path_history,
        target,
        telemetry,
    ):
        with self.lock:
            if self.busy:
                return

            self.busy = True
            self.status = "GEMMA വിധി വായിക്കുന്നു..."
            self.text = None
            self.error = None

        self.persona = random.choice(
            ORACLE_PERSONAS
        )

        threading.Thread(
            target=self._worker,
            args=(
                clean_frame.copy(),
                list(path_history),
                target,
                dict(telemetry),
                self.persona,
            ),
            daemon=True,
        ).start()

    @staticmethod
    def _encode_jpeg_base64(frame, quality=90):
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                int(cv2.IMWRITE_JPEG_QUALITY),
                int(quality),
            ],
        )

        if not ok:
            raise RuntimeError(
                "Could not encode Oracle image"
            )

        return base64.b64encode(
            encoded.tobytes()
        ).decode("ascii")

    def _ollama_chat(
        self,
        prompt,
        images=None,
        timeout=90,
    ):
        """Call Ollama /api/chat using only Python stdlib."""
        message = {
            "role": "user",
            "content": prompt,
        }

        if images:
            message["images"] = images

        payload = {
            "model": self.model,
            "messages": [message],
            "stream": False,
            "options": {
                "temperature": 1.0,
            },
        }

        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                raw = response.read().decode(
                    "utf-8"
                )

        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                pass

            raise RuntimeError(
                f"Ollama HTTP {exc.code}: {detail}"
            ) from exc

        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url}: "
                f"{exc.reason}"
            ) from exc

        data = json.loads(raw)

        text = (
            data.get("message", {})
            .get("content", "")
            .strip()
        )

        if not text:
            raise RuntimeError(
                "Ollama returned an empty Oracle response"
            )

        return text

    def _worker(
        self,
        clean_frame,
        path_history,
        target,
        telemetry,
        persona,
    ):
        try:
            diagnostic = self.build_private_path_image(
                clean_frame,
                path_history,
                target,
            )

            recent = load_history()[-4:]

            prompt = f"""
നിങ്ങൾ PottuAIയുടെ മലയാളം ഓണം ഓറക്കിൾ ആണ്.

ഈ റൗണ്ടിലെ നിങ്ങളുടെ കഥാപാത്രം:
{persona}

ഇത് Sundarikk Pottu Thodal കളിയുടെ ഒരു പൂർത്തിയായ ശ്രമമാണ്.

ചിത്രങ്ങൾ ലഭിച്ചിട്ടുണ്ടെങ്കിൽ:
1. ആദ്യ ചിത്രം = target എത്തിയ നിമിഷത്തിലെ clean final camera frame.
2. രണ്ടാം PRIVATE analysis image:
   - glowing white line = കളിക്കാരന്റെ മുഴുവൻ കൈയാത്ര
   - yellow circle = forehead target
   - cyan point = start
   - red point = final position

ഈ locally measured values ആണ് ground truth:
{json.dumps(telemetry, ensure_ascii=False, indent=2)}

ഈ ഉപകരണത്തിലെ സമീപകാല ശ്രമങ്ങൾ:
{json.dumps(recent, ensure_ascii=False, indent=2)}

നിങ്ങളുടെ ജോലി:
- കൈയാത്ര നേരെയാണോ, വളഞ്ഞതാണോ, shaky ആണോ, zig-zag ആണോ,
  വലിയ detour ഉണ്ടോ, അവസാന panic correction ഉണ്ടോ, controlled ആണോ എന്ന് വിലയിരുത്തുക.
- ചിത്രങ്ങൾ ലഭിച്ചിട്ടുണ്ടെങ്കിൽ path shape നേരിട്ട് പരിശോധിക്കുക.
- ചിത്രങ്ങൾ ലഭ്യമല്ലെങ്കിൽ telemetry, path efficiency, direction reversals,
  completion time, command counts എന്നിവ മാത്രം ആശ്രയിക്കുക.
- ground truth ന് വിരുദ്ധമായ claim പറയരുത്.

അവസാന Oracle output:
- മലയാളത്തിൽ മാത്രം.
- ഒരു continuous paragraph മാത്രം.
- heading ഇല്ല.
- bullets ഇല്ല.
- JSON ഇല്ല.
- 3 മുതൽ 5 വരെ sentences.
- ഏകദേശം 55 മുതൽ 90 വരെ മലയാളം words.
- witty + dramatic + mystical + sarcastic tone.
- playful teasing മാത്രം; insult ചെയ്യരുത്.
- മഹാബലിയെ സ്വാഭാവികമായി ഉൾപ്പെടുത്തുക.
- ഓരോ റൗണ്ടിലും wording വ്യത്യസ്തമാക്കുക.
- previous attempt ഉണ്ടെങ്കിൽ പ്രസക്തമായ ഒരു മാറ്റം മാത്രം പറയാം.
- exact technical measurements പറയേണ്ടതില്ല.
- "AI", "Gemini", "Gemma", "pixel", "telemetry" എന്നീ technical words ഉപയോഗിക്കരുത്.
- യഥാർത്ഥ supernatural certainty claim ചെയ്യരുത്.
- movement patternനോട് ബന്ധപ്പെട്ട ഒരു fun future-style prediction നൽകുക.

Output = Malayalam horoscope paragraph മാത്രം.
""".strip()

            # First choice: multimodal local Gemma through Ollama.
            clean_b64 = self._encode_jpeg_base64(
                clean_frame,
                quality=86,
            )
            path_b64 = self._encode_jpeg_base64(
                diagnostic,
                quality=90,
            )

            try:
                text = self._ollama_chat(
                    prompt,
                    images=[
                        clean_b64,
                        path_b64,
                    ],
                    timeout=120,
                )

                mode = "MULTIMODAL"

            except Exception as image_error:
                # Some Ollama/model combinations may reject images.
                # Do not fail the whole Oracle screen: retry text-only.
                print(
                    "Gemma multimodal request failed; "
                    "retrying telemetry-only: "
                    f"{image_error}"
                )

                text_only_prompt = (
                    prompt
                    + "\n\nIMPORTANT: ഈ requestൽ images ലഭ്യമല്ല. "
                    "മുകളിൽ നൽകിയ deterministic telemetry മാത്രം "
                    "ആശ്രയിച്ച് Oracle paragraph തയ്യാറാക്കുക."
                )

                text = self._ollama_chat(
                    text_only_prompt,
                    images=None,
                    timeout=120,
                )

                mode = "TELEMETRY"

            text = (
                text.strip()
                .strip('"')
                .strip("“")
                .strip("”")
            )

            with self.lock:
                self.text = text
                self.status = (
                    "LOCAL GEMMA ഓറക്കിൾ സംസാരിച്ചു"
                )

            save_history(
                telemetry,
                text,
                persona,
            )

            print()
            print(
                "========== LOCAL GEMMA ORACLE =========="
            )
            print(
                f"Server: {self.base_url}"
            )
            print(
                f"Model: {self.model} | Mode: {mode}"
            )
            print(text)
            print(
                "========================================"
            )
            print()

            # Read the LOCAL Gemma-generated Malayalam paragraph aloud
            # through the existing Gemini Live voice layer.
            live_voice.speak_oracle(
                text
            )

        except Exception as exc:
            print(
                "Local Gemma Oracle error: "
                f"{exc}"
            )

            fallback = self._fallback(
                telemetry
            )

            with self.lock:
                self.error = str(exc)
                self.text = fallback
                self.status = (
                    "LOCAL GEMMA OFFLINE"
                )

            # Preserve the demo even if the LAN/Ollama machine is down.
            live_voice.speak_oracle(
                fallback
            )

        finally:
            with self.lock:
                self.busy = False

    @staticmethod
    def _fallback(
        telemetry,
    ):
        reversals = telemetry.get(
            "direction_reversals",
            0,
        )

        efficiency = telemetry.get(
            "path_efficiency",
            0,
        )

        if (
            efficiency >= 0.78
            and reversals <= 2
        ):
            return (
                "നിന്റെ കൈ ഇന്ന് സംശയങ്ങൾക്ക് സമയം കൊടുക്കാതെ "
                "നേരെ ലക്ഷ്യത്തിലേക്ക് നീങ്ങിയതാണ് കാണുന്നത്. "
                "ഇതേ ശാന്തത തുടരുകയാണെങ്കിൽ അടുത്ത വെല്ലുവിളിയിലും "
                "അവസാന നിമിഷം നിന്റെ പക്ഷത്തായിരിക്കും. "
                "മഹാബലി പോലും ഈ ആത്മവിശ്വാസം കണ്ടിട്ട് "
                "രഹസ്യം ചോദിക്കാതെ ഒരു ചിരിയോടെ കടന്നുപോകും."
            )

        if reversals >= 5:
            return (
                "നിന്റെ കൈ ലക്ഷ്യം കണ്ടെത്തുന്നതിന് മുമ്പ് "
                "സ്വന്തമായി ഒരു ചെറിയ ഓണം യാത്ര നടത്തിയതുപോലെ തോന്നുന്നു. "
                "വളവും തിരിവും ഉണ്ടായിട്ടും അവസാനം ശരിയായ സ്ഥലം "
                "കണ്ടെത്തിയത് നിന്റെ പ്രത്യേക കഴിവാണ്. "
                "അടുത്ത തവണ അല്പം കുറച്ച് സംശയിച്ചാൽ വിജയം "
                "കൂടുതൽ വേഗം എത്തും; മഹാബലി ഇതിനകം നിനക്കായി "
                "ഒരു ഭൂപടം കരുതിയിരിക്കാം."
            )

        return (
            "നിന്റെ കൈയുടെ യാത്ര ആദ്യം അല്പം സംശയിച്ചെങ്കിലും "
            "അവസാനത്തിൽ ലക്ഷ്യവുമായി നല്ലൊരു ധാരണയിലെത്തി. "
            "ഇങ്ങനെ അവസാന നിമിഷം ശാന്തമായി നിയന്ത്രിക്കാൻ കഴിഞ്ഞാൽ "
            "അടുത്ത വെല്ലുവിളിയും നിനക്ക് അനുകൂലമായി തീരാൻ സാധ്യതയുണ്ട്. "
            "മഹാബലി ഈ നീക്കം ശ്രദ്ധിച്ചിട്ടുണ്ടാകും, പക്ഷേ "
            "നിന്റെ രഹസ്യം ഇപ്പോൾ സുരക്ഷിതമാണ്."
        )


oracle = GeminiOracle()


# ------------------------------------------------------------
# Vision helpers
# ------------------------------------------------------------

def load_face_detector():
    candidates = [
        (
            "/usr/share/opencv4/"
            "haarcascades/"
            "haarcascade_frontalface_default.xml"
        ),
        (
            cv2.data.haarcascades
            + "haarcascade_frontalface_default.xml"
        ),
    ]

    for path in candidates:
        if (
            path
            and os.path.exists(path)
        ):
            detector = (
                cv2.CascadeClassifier(
                    path
                )
            )

            if not detector.empty():
                print(
                    "Face detector: "
                    f"{path}"
                )

                return detector

    raise RuntimeError(
        "Could not load "
        "Haar face detector"
    )


def detect_red_marker(
    frame,
):
    hsv = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2HSV,
    )

    lower_1 = np.array(
        [0, 120, 70],
        dtype=np.uint8,
    )

    upper_1 = np.array(
        [10, 255, 255],
        dtype=np.uint8,
    )

    lower_2 = np.array(
        [170, 120, 70],
        dtype=np.uint8,
    )

    upper_2 = np.array(
        [180, 255, 255],
        dtype=np.uint8,
    )

    mask = cv2.inRange(
        hsv,
        lower_1,
        upper_1,
    )

    mask = cv2.bitwise_or(
        mask,
        cv2.inRange(
            hsv,
            lower_2,
            upper_2,
        ),
    )

    kernel = np.ones(
        (5, 5),
        dtype=np.uint8,
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        kernel,
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel,
    )

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    best = None
    best_area = 0.0

    for contour in contours:
        area = cv2.contourArea(
            contour
        )

        if (
            area < RED_MIN_AREA
            or area <= best_area
        ):
            continue

        moments = cv2.moments(
            contour
        )

        if moments["m00"] == 0:
            continue

        x = int(
            moments["m10"]
            / moments["m00"]
        )

        y = int(
            moments["m01"]
            / moments["m00"]
        )

        best = (
            x,
            y,
        )

        best_area = area

    return best


def verified_direction(
    dx,
    dy,
    distance,
):
    if (
        distance
        <= TARGET_TOLERANCE
    ):
        return "STOP"

    if (
        abs(dx)
        <= AXIS_TOLERANCE
        and abs(dy)
        > AXIS_TOLERANCE
    ):
        return (
            "DOWN"
            if dy > 0
            else "UP"
        )

    if (
        abs(dy)
        <= AXIS_TOLERANCE
        and abs(dx)
        > AXIS_TOLERANCE
    ):
        return (
            "RIGHT"
            if dx > 0
            else "LEFT"
        )

    if abs(dx) >= abs(dy):
        return (
            "RIGHT"
            if dx > 0
            else "LEFT"
        )

    return (
        "DOWN"
        if dy > 0
        else "UP"
    )


def add_path_point(
    path,
    point,
):
    if not path:
        path.append(point)
        return

    x1, y1 = path[-1]
    x2, y2 = point

    if (
        math.hypot(
            x2 - x1,
            y2 - y1,
        )
        > PATH_SAMPLE_DISTANCE
    ):
        path.append(point)


def path_length(
    path,
):
    total = 0.0

    for index in range(
        1,
        len(path),
    ):
        x1, y1 = path[
            index - 1
        ]

        x2, y2 = path[
            index
        ]

        total += math.hypot(
            x2 - x1,
            y2 - y1,
        )

    return total


def direction_reversals(
    commands,
):
    opposite = {
        (
            "LEFT",
            "RIGHT",
        ),
        (
            "RIGHT",
            "LEFT",
        ),
        (
            "UP",
            "DOWN",
        ),
        (
            "DOWN",
            "UP",
        ),
    }

    total = 0

    for index in range(
        1,
        len(commands),
    ):
        if (
            commands[
                index - 1
            ],
            commands[
                index
            ],
        ) in opposite:
            total += 1

    return total


def make_telemetry(
    path,
    commands,
    command_counts,
    target,
    final_error,
    completion_time,
):
    travelled = path_length(
        path
    )

    if path:
        direct = math.hypot(
            target[0]
            - path[0][0],
            target[1]
            - path[0][1],
        )

    else:
        direct = 0.0

    efficiency = (
        direct / travelled
        if travelled > 0
        else 0.0
    )

    efficiency = max(
        0.0,
        min(
            1.0,
            efficiency,
        ),
    )

    return {
        "final_error": int(
            final_error
        ),
        "completion_time_seconds": round(
            completion_time,
            2,
        ),
        "sampled_path_points": len(
            path
        ),
        "path_length": round(
            travelled,
            1,
        ),
        "direct_start_to_target_distance": round(
            direct,
            1,
        ),
        "path_efficiency": round(
            efficiency,
            3,
        ),
        "direction_reversals": (
            direction_reversals(
                commands
            )
        ),
        "command_counts": dict(
            command_counts
        ),
    }


# ------------------------------------------------------------
# Convert OpenCV frame -> QPixmap
# ------------------------------------------------------------

def frame_to_pixmap(
    frame,
):
    rgb = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB,
    )

    height, width, channels = (
        rgb.shape
    )

    bytes_per_line = (
        channels * width
    )

    image = QImage(
        rgb.data,
        width,
        height,
        bytes_per_line,
        QImage.Format_RGB888,
    ).copy()

    return QPixmap.fromImage(
        image
    )


# ------------------------------------------------------------
# Qt Oracle UI
# ------------------------------------------------------------

class OracleWindow(
    QWidget
):
    """
    Mimics the UI reference:
      dark cinematic background
      purple title
      centered quoted horoscope
      minimal controls
    """

    def __init__(
        self,
        frozen_frame,
    ):
        super().__init__()

        self.action = None
        self.loop = QEventLoop()

        self.setWindowTitle(
            "PottuAI Oracle"
        )

        self.setMinimumSize(
            800,
            520,
        )

        self.setStyleSheet(
            """
            QWidget {
                background: #050506;
                color: #f7f7f8;
            }

            QFrame#oracleCard {
                background-color:
                    rgba(4, 4, 6, 238);
                border:
                    1px solid rgba(
                        255, 255, 255, 20
                    );
                border-radius: 26px;
            }

            QLabel#oracleTitle {
                color: #c86cff;
                background: transparent;
            }

            QLabel#oracleText {
                color: #f5f5f6;
                background: transparent;
            }

            QLabel#statusText {
                color: #83838d;
                background: transparent;
            }

            QPushButton {
                color: #eeeeF2;
                background: #18181d;
                border: 1px solid #303037;
                border-radius: 18px;
                padding: 11px 24px;
                font-weight: 600;
            }

            QPushButton:hover {
                background: #24242b;
                border-color: #c86cff;
            }

            QPushButton#restartButton {
                background: #8b3dcc;
                border: none;
                color: white;
            }

            QPushButton#restartButton:hover {
                background: #a451e5;
            }
            """
        )

        # -------------------------
        # Background frame
        # -------------------------

        self.background = QLabel(
            self
        )

        self.background.setPixmap(
            frame_to_pixmap(
                frozen_frame
            )
        )

        self.background.setScaledContents(
            True
        )

        # Dark overlay
        self.dark_overlay = QFrame(
            self
        )

        self.dark_overlay.setStyleSheet(
            """
            background-color:
                rgba(0, 0, 0, 218);
            """
        )

        # -------------------------
        # Card
        # -------------------------

        self.card = QFrame(
            self
        )

        self.card.setObjectName(
            "oracleCard"
        )

        shadow = (
            QGraphicsDropShadowEffect(
                self
            )
        )

        shadow.setBlurRadius(
            55
        )

        shadow.setOffset(
            0,
            10,
        )

        self.card.setGraphicsEffect(
            shadow
        )

        layout = QVBoxLayout(
            self.card
        )

        layout.setContentsMargins(
            55,
            44,
            55,
            36,
        )

        layout.setSpacing(
            24
        )

        layout.addStretch(
            1
        )

        # -------------------------
        # Title
        # -------------------------

        self.title = QLabel(
            "ഓറക്കിൾ സംസാരിക്കുന്നു"
        )

        self.title.setObjectName(
            "oracleTitle"
        )

        self.title.setAlignment(
            Qt.AlignCenter
        )

        title_font = QFont(
            MALAYALAM_FONT_FAMILY,
            25,
        )

        title_font.setBold(
            True
        )

        self.title.setFont(
            title_font
        )

        layout.addWidget(
            self.title
        )

        # -------------------------
        # Horoscope text
        # -------------------------

        self.oracle_label = QLabel(
            "നിങ്ങളുടെ കൈയാത്രയിൽ നിന്ന് "
            "വിധി വായിക്കുന്നു..."
        )

        self.oracle_label.setObjectName(
            "oracleText"
        )

        self.oracle_label.setAlignment(
            Qt.AlignCenter
        )

        self.oracle_label.setWordWrap(
            True
        )

        self.oracle_label.setTextFormat(
            Qt.PlainText
        )

        # Critical for Malayalam:
        # reserve real vertical space and add breathing room above/below
        # so vowel marks / glyph extents are never clipped.
        self.oracle_label.setContentsMargins(
            24,
            28,
            24,
            32,
        )

        self.oracle_label.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )

        self.oracle_label.setMinimumHeight(
            220
        )

        self.oracle_label.setMaximumWidth(
            1200
        )

        self.oracle_font_size = 20

        text_font = QFont(
            MALAYALAM_FONT_FAMILY,
            self.oracle_font_size,
        )

        self.oracle_label.setFont(
            text_font
        )

        # Give the horoscope area most of the available card height.
        layout.addWidget(
            self.oracle_label,
            8,
            Qt.AlignCenter,
        )

        layout.addSpacing(
            10
        )

        # -------------------------
        # Status
        # -------------------------

        self.status_label = QLabel(
            "Local Gemma 4 • gemma4:e2b"
        )

        self.status_label.setObjectName(
            "statusText"
        )

        self.status_label.setAlignment(
            Qt.AlignCenter
        )

        status_font = QFont(
            MALAYALAM_FONT_FAMILY,
            10,
        )

        self.status_label.setFont(
            status_font
        )

        layout.addWidget(
            self.status_label
        )

        # -------------------------
        # Buttons
        # -------------------------

        button_row = QHBoxLayout()

        button_row.addStretch()

        self.restart_button = QPushButton(
            "വീണ്ടും കളിക്കുക"
        )

        self.restart_button.setObjectName(
            "restartButton"
        )

        restart_font = QFont(
            MALAYALAM_FONT_FAMILY,
            12,
        )

        self.restart_button.setFont(
            restart_font
        )

        self.restart_button.clicked.connect(
            self.restart
        )

        button_row.addWidget(
            self.restart_button
        )

        self.quit_button = QPushButton(
            "പുറത്തുകടക്കുക"
        )

        quit_font = QFont(
            MALAYALAM_FONT_FAMILY,
            12,
        )

        self.quit_button.setFont(
            quit_font
        )

        self.quit_button.clicked.connect(
            self.quit_app
        )

        button_row.addWidget(
            self.quit_button
        )

        button_row.addStretch()

        layout.addLayout(
            button_row
        )

        # Disable restart while Gemini is generating.
        self.restart_button.setEnabled(
            False
        )

        # Poll oracle worker safely from Qt thread.
        self.timer = QTimer(
            self
        )

        self.timer.setInterval(
            120
        )

        self.timer.timeout.connect(
            self.refresh_oracle
        )

        self.timer.start()

    def _fit_oracle_text(self):
        """
        Dynamically shrink the Malayalam horoscope font until the complete
        paragraph fits inside the available label height.

        This prevents the first and last lines from being clipped on
        Raspberry Pi displays with different resolutions/DPI scaling.
        """
        if not self.oracle_label.text():
            return

        available_width = max(
            320,
            self.oracle_label.width()
            - 52,
        )

        available_height = max(
            180,
            self.oracle_label.height()
            - 64,
        )

        # Start large, then shrink only if required.
        for point_size in range(22, 13, -1):
            font = QFont(
                MALAYALAM_FONT_FAMILY,
                point_size,
            )

            self.oracle_label.setFont(
                font
            )

            metrics = self.oracle_label.fontMetrics()

            bounding = metrics.boundingRect(
                0,
                0,
                available_width,
                10000,
                int(
                    Qt.TextWordWrap
                    | Qt.AlignHCenter
                ),
                self.oracle_label.text(),
            )

            # Add extra safety for Malayalam vowel marks and line spacing.
            required_height = (
                bounding.height()
                + metrics.lineSpacing()
                + 18
            )

            if required_height <= available_height:
                self.oracle_font_size = point_size
                break

    def resizeEvent(
        self,
        event,
    ):
        size = self.size()

        self.background.setGeometry(
            0,
            0,
            size.width(),
            size.height(),
        )

        self.dark_overlay.setGeometry(
            0,
            0,
            size.width(),
            size.height(),
        )

        margin_x = max(
            24,
            int(
                size.width()
                * 0.055
            ),
        )

        margin_y = max(
            18,
            int(
                size.height()
                * 0.045
            ),
        )

        self.card.setGeometry(
            margin_x,
            margin_y,
            (
                size.width()
                - margin_x * 2
            ),
            (
                size.height()
                - margin_y * 2
            ),
        )

        # Re-fit text after the card/label geometry changes.
        QTimer.singleShot(
            0,
            self._fit_oracle_text,
        )

        super().resizeEvent(
            event
        )

    def refresh_oracle(
        self,
    ):
        self.status_label.setText(
            oracle.status
        )

        if oracle.text:
            self.oracle_label.setText(
                "“"
                + oracle.text
                + "”"
            )

            QTimer.singleShot(
                0,
                self._fit_oracle_text,
            )

            self.restart_button.setEnabled(
                True
            )

            self.timer.stop()

    def restart(
        self,
    ):
        self.action = "RESTART"

        self.close()

        if self.loop.isRunning():
            self.loop.quit()

    def quit_app(
        self,
    ):
        self.action = "QUIT"

        self.close()

        if self.loop.isRunning():
            self.loop.quit()

    def keyPressEvent(
        self,
        event,
    ):
        if event.key() in (
            Qt.Key_Q,
            Qt.Key_Escape,
        ):
            self.quit_app()
            return

        if (
            event.key()
            == Qt.Key_R
            and self.restart_button.isEnabled()
        ):
            self.restart()
            return

        super().keyPressEvent(
            event
        )

    def closeEvent(
        self,
        event,
    ):
        if self.action is None:
            self.action = "QUIT"

        if self.loop.isRunning():
            self.loop.quit()

        event.accept()

    def exec_result(
        self,
    ):
        # Fullscreen is ideal for the demo.
        self.showFullScreen()

        self.raise_()
        self.activateWindow()

        self.loop.exec()

        return self.action


# ------------------------------------------------------------
# One OpenCV game round
# ------------------------------------------------------------

def run_game_round(
    face_detector,
):
    live_voice.reset_round()
    oracle.reset()
    esp32.reset()

    if sys.platform.startswith(
        "linux"
    ):
        cap = cv2.VideoCapture(
            CAMERA_INDEX,
            cv2.CAP_V4L2,
        )
    else:
        cap = cv2.VideoCapture(
            CAMERA_INDEX
        )

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera "
            f"{CAMERA_INDEX}"
        )

    path_history = []

    command_history = []

    command_counts = {
        "LEFT": 0,
        "RIGHT": 0,
        "UP": 0,
        "DOWN": 0,
        "STOP": 0,
    }

    last_counted = None

    started_at = time.monotonic()

    try:
        while cap.isOpened():
            ok, frame = cap.read()

            if not ok:
                print(
                    "Unable to read camera."
                )
                return None

            # Preserve a clean camera frame BEFORE overlays.
            clean_frame = frame.copy()

            gray = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY,
            )

            faces = (
                face_detector.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(60, 60),
                )
            )

            target = None

            if len(faces) > 0:
                x, y, w, h = max(
                    faces,
                    key=lambda rect: (
                        rect[2]
                        * rect[3]
                    ),
                )

                target = (
                    int(
                        x + w / 2
                    ),
                    int(
                        y + h * 0.30
                    ),
                )

                cv2.rectangle(
                    frame,
                    (
                        x,
                        y,
                    ),
                    (
                        x + w,
                        y + h,
                    ),
                    (
                        255,
                        0,
                        0,
                    ),
                    2,
                )

                cv2.circle(
                    frame,
                    target,
                    TARGET_TOLERANCE,
                    (
                        0,
                        255,
                        255,
                    ),
                    2,
                )

            marker = detect_red_marker(
                frame
            )

            if marker:
                add_path_point(
                    path_history,
                    marker,
                )

                # Current marker only.
                # NO path is drawn.
                cv2.circle(
                    frame,
                    marker,
                    9,
                    (
                        0,
                        0,
                        255,
                    ),
                    -1,
                )

            if (
                target
                and marker
            ):
                dx = (
                    target[0]
                    - marker[0]
                )

                dy = (
                    target[1]
                    - marker[1]
                )

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

                if command != last_counted:
                    command_history.append(
                        command
                    )

                    command_counts[
                        command
                    ] += 1

                    last_counted = command

                # Same verified command:
                # -> ESP32
                # -> Gemini Live Malayalam voice
                esp32.send(
                    command
                )

                live_voice.guide(
                    command,
                    dx,
                    dy,
                    distance,
                )

                command_color = (
                    (
                        0,
                        255,
                        0,
                    )
                    if command == "STOP"
                    else (
                        0,
                        255,
                        255,
                    )
                )

                # Keep OpenCV HUD ASCII/English only.
                # This avoids Malayalam box rendering.
                cv2.putText(
                    frame,
                    (
                        "COMMAND: "
                        + command
                    ),
                    (
                        20,
                        38,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    command_color,
                    2,
                    cv2.LINE_AA,
                )

                cv2.putText(
                    frame,
                    (
                        "DISTANCE: "
                        f"{distance}px"
                    ),
                    (
                        20,
                        72,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (
                        255,
                        255,
                        255,
                    ),
                    1,
                    cv2.LINE_AA,
                )

                cv2.putText(
                    frame,
                    (
                        "GEMINI LIVE: "
                        + live_voice.status
                    ),
                    (
                        20,
                        frame.shape[0]
                        - 18,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.44,
                    (
                        255,
                        255,
                        255,
                    ),
                    1,
                    cv2.LINE_AA,
                )

                if command == "STOP":
                    completion_time = (
                        time.monotonic()
                        - started_at
                    )

                    final_error = distance

                    telemetry = (
                        make_telemetry(
                            path_history,
                            command_history,
                            command_counts,
                            target,
                            final_error,
                            completion_time,
                        )
                    )

                    # Keep clean frame with no OpenCV debug overlays
                    # for the cinematic Qt result background.
                    frozen_clean_frame = (
                        clean_frame.copy()
                    )

                    # --------------------------------
                    # CRITICAL:
                    # CLOSE CAMERA + OPENCV UI FIRST
                    # --------------------------------
                    cap.release()

                    esp32.send(
                        "STOP"
                    )

                    cv2.destroyAllWindows()

                    # Give desktop compositor a moment to remove
                    # the OpenCV window before opening Qt.
                    time.sleep(
                        0.15
                    )

                    print()
                    print(
                        "TARGET REACHED"
                    )
                    print(
                        "Camera closed."
                    )
                    print(
                        "Opening Qt Oracle UI..."
                    )
                    print()

                    return {
                        "frame": frozen_clean_frame,
                        "path": list(
                            path_history
                        ),
                        "target": target,
                        "telemetry": telemetry,
                    }

            else:
                cv2.putText(
                    frame,
                    "WAITING FOR FACE / RED MARKER",
                    (
                        20,
                        38,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.60,
                    (
                        0,
                        170,
                        255,
                    ),
                    2,
                    cv2.LINE_AA,
                )

                cv2.putText(
                    frame,
                    (
                        "GEMINI LIVE: "
                        + live_voice.status
                    ),
                    (
                        20,
                        frame.shape[0]
                        - 18,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.44,
                    (
                        255,
                        255,
                        255,
                    ),
                    1,
                    cv2.LINE_AA,
                )

            cv2.imshow(
                WINDOW_NAME,
                frame,
            )

            key = (
                cv2.waitKey(1)
                & 0xFF
            )

            if key in (
                ord("q"),
                27,
            ):
                return {
                    "quit": True
                }

    finally:
        if cap.isOpened():
            cap.release()

        cv2.destroyAllWindows()


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    global MALAYALAM_FONT_FAMILY

    # Qt app must exist before loading application fonts.
    qt_app = QApplication.instance()

    if qt_app is None:
        qt_app = QApplication(
            sys.argv
        )

    qt_app.setApplicationName(
        "PottuAI"
    )

    MALAYALAM_FONT_FAMILY = (
        install_app_font()
    )

    detector = (
        load_face_detector()
    )

    print()
    print(
        "PottuAI Oracle Edition"
    )
    print(
        f"Live voice: {LIVE_MODEL}"
    )
    print(
        f"Oracle: {ORACLE_MODEL} @ {OLLAMA_BASE_URL}"
    )
    print()

    if not os.environ.get(
        "GEMINI_API_KEY"
    ):
        print(
            "WARNING: GEMINI_API_KEY "
            "is not set."
        )
        print()
    else:
        # Build/download the five tiny Gemini Malayalam direction clips
        # BEFORE gameplay. Subsequent runs reuse the local cache.
        print(
            "Preparing fast Gemini Malayalam guidance..."
        )
        live_voice.wait_until_ready(
            timeout=30
        )
        print(
            f"Gemini voice status: "
            f"{live_voice.status}"
        )
        print()

    try:
        while True:
            round_data = (
                run_game_round(
                    detector
                )
            )

            if not round_data:
                break

            if round_data.get(
                "quit"
            ):
                break

            # Start local Gemma 4 Oracle analysis immediately.
            oracle.start(
                clean_frame=round_data[
                    "frame"
                ],
                path_history=round_data[
                    "path"
                ],
                target=round_data[
                    "target"
                ],
                telemetry=round_data[
                    "telemetry"
                ],
            )

            # Camera/OpenCV are already closed.
            # Now show only the proper Unicode Qt UI.
            result_window = (
                OracleWindow(
                    round_data[
                        "frame"
                    ]
                )
            )

            action = (
                result_window.exec_result()
            )

            result_window.deleteLater()

            qt_app.processEvents()

            if action != "RESTART":
                break

    finally:
        esp32.send(
            "STOP"
        )

        live_voice.close()

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
