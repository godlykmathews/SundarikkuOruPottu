import cv2
import sys


class Camera:
    def __init__(self, config):
        self.config = config
        self.cap = None

    def start(self):
        if sys.platform == "darwin":
            backend = cv2.CAP_AVFOUNDATION
        elif sys.platform.startswith("linux"):
            backend = cv2.CAP_V4L2
        else:
            backend = cv2.CAP_ANY

        self.cap = cv2.VideoCapture(
            self.config.CAMERA_INDEX,
            backend
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.config.FRAME_WIDTH
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.config.FRAME_HEIGHT
        )

        if not self.cap.isOpened():
            raise RuntimeError("Camera could not be opened")

        print("Camera started")

    def get_frame(self):
        ret, frame = self.cap.read()

        if not ret:
            return None

        if self.config.FLIP_CAMERA:
            frame = cv2.flip(frame, 1)

        return frame

    def stop(self):
        if self.cap:
            self.cap.release()
