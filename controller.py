import math
import time


class Controller:

    def __init__(self, config, audio):

        self.config = config
        self.audio = audio

        self.last_command = None
        self.last_voice_time = 0

    def update(self, target, pen):

        if target is None:

            return {
                "command": "NO FACE",
                "distance": 0
            }

        if pen is None:

            return {
                "command": "FIND STICK",
                "distance": 0
            }

        target_x, target_y = target
        pen_x, pen_y = pen

        dx = target_x - pen_x
        dy = target_y - pen_y

        distance = math.sqrt(
            dx ** 2 + dy ** 2
        )

        # Reached target
        if distance <= self.config.TARGET_TOLERANCE:

            command = "PERFECT"

        else:

            # Decide whether horizontal
            # or vertical movement is larger

            if abs(dx) > abs(dy):

                if dx > self.config.DIRECTION_THRESHOLD:
                    command = "RIGHT"

                elif dx < -self.config.DIRECTION_THRESHOLD:
                    command = "LEFT"

                else:
                    command = "STOP"

            else:

                if dy > self.config.DIRECTION_THRESHOLD:
                    command = "DOWN"

                elif dy < -self.config.DIRECTION_THRESHOLD:
                    command = "UP"

                else:
                    command = "STOP"

        self.voice(command)

        return {
            "command": command,
            "distance": distance,
            "dx": dx,
            "dy": dy
        }

    def voice(self, command):

        current_time = time.time()

        if command == "PERFECT":

            if self.last_command != "PERFECT":

                self.audio.say(
                    "Perfect! Pottu thodu!"
                )

                self.last_command = command

            return

        if (
            command != self.last_command
            or
            current_time - self.last_voice_time
            > self.config.AUDIO_COOLDOWN
        ):

            messages = {

                "LEFT":
                    "Left",

                "RIGHT":
                    "Right",

                "UP":
                    "Up",

                "DOWN":
                    "Down",

                "STOP":
                    "Stop"
            }

            if command in messages:

                self.audio.say(
                    messages[command]
                )

            self.last_command = command
            self.last_voice_time = current_time