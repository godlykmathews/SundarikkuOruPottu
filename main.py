import cv2

from config import Config
from camera import Camera
from face_detector import FaceDetector
from target import TargetDetector
from pen_tracker import PenTracker
from controller import Controller
from audio import Audio


def main():

    print("==============================")
    print(" SUNDARIKKU POTTU THODUNA AI ")
    print("==============================")

    config = Config()

    camera = Camera(config)

    face_detector = FaceDetector()

    target_detector = TargetDetector(
        config
    )

    pen_tracker = PenTracker(
        config
    )

    audio = Audio()

    controller = Controller(
        config,
        audio
    )

    camera.start()

    print("Ready!")
    print("Press Q to exit")

    while True:

        frame = camera.get_frame()

        if frame is None:
            print("Camera frame error")
            break

        # -------------------------
        # FACE DETECTION
        # -------------------------

        face_point = face_detector.detect(
            frame
        )

        # -------------------------
        # TARGET DETECTION
        # -------------------------

        target = target_detector.get_target(
            face_point,
            frame.shape
        )

        # -------------------------
        # STICK DETECTION
        # -------------------------

        pen = pen_tracker.detect(
            frame
        )

        # -------------------------
        # GAME LOGIC
        # -------------------------

        result = controller.update(
            target,
            pen
        )

        # -------------------------
        # DISPLAY TARGET
        # -------------------------

        if target:

            cv2.circle(
                frame,
                target,
                config.TARGET_TOLERANCE,
                (0, 255, 255),
                2
            )

            cv2.circle(
                frame,
                target,
                4,
                (0, 255, 255),
                -1
            )

        # -------------------------
        # DISPLAY PEN
        # -------------------------

        if pen:

            cv2.circle(
                frame,
                pen,
                8,
                (0, 0, 255),
                -1
            )

        # -------------------------
        # CONNECT PEN -> TARGET
        # -------------------------

        if target and pen:

            cv2.line(
                frame,
                pen,
                target,
                (255, 255, 255),
                2
            )

        # -------------------------
        # DISPLAY COMMAND
        # -------------------------

        command = result["command"]

        cv2.putText(
            frame,
            command,
            (20, 50),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.3,
            (0, 255, 0),
            3
        )

        # -------------------------
        # DISPLAY
        # -------------------------

        cv2.imshow(
            "Sundarikku Pottu Thoduna",
            frame
        )

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break

    camera.stop()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
