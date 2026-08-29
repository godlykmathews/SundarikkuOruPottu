import cv2
import numpy as np


class PenTracker:
    """Locate a small, saturated red marker tip in a camera frame."""

    def __init__(self, config):
        self.config = config
        self.previous_pen = None
        self.missing_frames = 0
        self.kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (3, 3)
        )

    def detect(self, frame):
        if frame is None or frame.size == 0:
            self.previous_pen = None
            self.missing_frames = 0
            return None

        hsv = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2HSV
        )

        blue, green, red = cv2.split(frame)
        strongest_non_red = cv2.max(blue, green)
        red_difference = cv2.subtract(
            red,
            strongest_non_red
        )

        _, red_dominant = cv2.threshold(
            red_difference,
            40,
            255,
            cv2.THRESH_BINARY
        )

        # Red wraps around the ends of OpenCV's HSV hue range, so both
        # configured ranges are required.
        lower_red = cv2.inRange(
            hsv,
            self.config.RED_LOWER_1,
            self.config.RED_UPPER_1
        )

        upper_red = cv2.inRange(
            hsv,
            self.config.RED_LOWER_2,
            self.config.RED_UPPER_2
        )

        mask = cv2.bitwise_or(
            lower_red,
            upper_red
        )

        # Skin can have a low HSV hue like red. Requiring the red channel to
        # clearly dominate both green and blue removes most skin regions.
        mask = cv2.bitwise_and(
            mask,
            red_dominant
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            self.kernel
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            self.kernel
        )

        contours, _ = cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        frame_scale = (
            frame.shape[0]
            * frame.shape[1]
            / (640 * 480)
        )
        length_scale = frame_scale ** 0.5

        min_area = self.config.PEN_MIN_AREA * frame_scale
        max_area = self.config.PEN_MAX_AREA * frame_scale
        max_dimension = (
            self.config.PEN_MAX_DIMENSION
            * length_scale
        )

        candidates = []

        for contour in contours:
            area = cv2.contourArea(contour)

            if not min_area <= area <= max_area:
                continue

            _, _, width, height = cv2.boundingRect(contour)

            if width > max_dimension or height > max_dimension:
                continue

            moments = cv2.moments(contour)

            if moments["m00"] == 0:
                continue

            x = int(moments["m10"] / moments["m00"])
            y = int(moments["m01"] / moments["m00"])

            contour_mask = np.zeros(
                mask.shape,
                dtype=np.uint8
            )
            cv2.drawContours(
                contour_mask,
                [contour],
                -1,
                255,
                -1
            )

            mean_saturation = cv2.mean(
                hsv[:, :, 1],
                mask=contour_mask
            )[0]
            mean_red_difference = cv2.mean(
                red_difference,
                mask=contour_mask
            )[0]

            colour_score = (
                mean_saturation
                + 2 * mean_red_difference
            )

            candidates.append(
                ((x, y), colour_score)
            )

        if not candidates:
            return self._mark_missing()

        if self.previous_pen is None:
            new_pen, _ = max(
                candidates,
                key=lambda candidate: candidate[1]
            )

        else:
            new_pen, _ = min(
                candidates,
                key=lambda candidate: self._distance(
                    candidate[0],
                    self.previous_pen
                )
            )

            max_jump = (
                self.config.PEN_MAX_JUMP
                * length_scale
            )

            if (
                self._distance(new_pen, self.previous_pen)
                > max_jump
            ):
                return self._mark_missing()

        self.missing_frames = 0
        x, y = new_pen

        if self.previous_pen is None:
            self.previous_pen = new_pen

        else:
            alpha = self.config.SMOOTHING_FACTOR

            self.previous_pen = (
                int((1 - alpha) * x + alpha * self.previous_pen[0]),
                int((1 - alpha) * y + alpha * self.previous_pen[1])
            )

        return self.previous_pen

    def _mark_missing(self):
        self.missing_frames += 1

        if (
            self.missing_frames
            >= self.config.PEN_REACQUIRE_FRAMES
        ):
            self.previous_pen = None

        return None

    @staticmethod
    def _distance(first, second):
        dx = first[0] - second[0]
        dy = first[1] - second[1]

        return (dx ** 2 + dy ** 2) ** 0.5
