from pathlib import Path

import cv2


class FaceDetector:
    def __init__(self):
        model_path = (
            Path(__file__).parent
            / "models"
            / "face_detection_yunet_2026may.onnx"
        )

        if not model_path.is_file():
            raise FileNotFoundError(
                f"Face detection model not found: {model_path}"
            )

        self.face_detector = cv2.FaceDetectorYN.create(
            model=str(model_path),
            config="",
            input_size=(320, 320),
            score_threshold=0.75,
            nms_threshold=0.3,
            top_k=5000,
            backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
            target_id=cv2.dnn.DNN_TARGET_CPU
        )
        self.input_size = None

    def detect(self, frame):
        if frame is None or frame.size == 0:
            return None

        height, width = frame.shape[:2]
        input_size = (width, height)

        if input_size != self.input_size:
            self.face_detector.setInputSize(input_size)
            self.input_size = input_size

        _, faces = self.face_detector.detect(
            frame
        )

        if faces is None:
            return None

        face = max(
            faces,
            key=lambda detected_face: (
                detected_face[2]
                * detected_face[3]
            )
        )

        right_eye_x, right_eye_y = face[4:6]
        left_eye_x, left_eye_y = face[6:8]

        # Return the normalized midpoint between the eyes. TargetDetector
        # shifts it upward to the pottu position.
        return (
            float(right_eye_x + left_eye_x) / (2 * width),
            float(right_eye_y + left_eye_y) / (2 * height)
        )
