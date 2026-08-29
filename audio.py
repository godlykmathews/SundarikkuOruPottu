import subprocess
import threading
import shutil
import sys


class Audio:

    def __init__(self):
        self.busy = False

        if sys.platform == "darwin":
            self.speech_command = shutil.which("say")
        else:
            self.speech_command = shutil.which("espeak")

    def say(self, text):

        if self.busy or self.speech_command is None:
            return

        thread = threading.Thread(
            target=self._speak,
            args=(text,),
            daemon=True
        )

        thread.start()

    def _speak(self, text):

        self.busy = True

        try:

            if sys.platform == "darwin":
                command = [
                    self.speech_command,
                    "-r",
                    "150",
                    text
                ]

            else:
                command = [
                    self.speech_command,
                    "-s",
                    "150",
                    text
                ]

            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        finally:

            self.busy = False
