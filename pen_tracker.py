import cv2


class PenTracker:
    """Locate the centre of the largest red marker in a camera frame."""

    def __init__(self, config):
        self.config = config
        self.previous_pen = None
        self.kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (5, 5)
        )

    def detect(self, frame):
        if frame is None or frame.size == 0:
            self.previous_pen = None
            return None

        hsv = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2HSV
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

        if not contours:
            self.previous_pen = None
            return None

        contour = max(
            contours,
            key=cv2.contourArea
        )

        if cv2.contourArea(contour) < self.config.PEN_MIN_AREA:
            self.previous_pen = None
            return None

        moments = cv2.moments(contour)

        if moments["m00"] == 0:
            self.previous_pen = None
            return None

        x = int(moments["m10"] / moments["m00"])
        y = int(moments["m01"] / moments["m00"])
        new_pen = (x, y)

        if self.previous_pen is None:
            self.previous_pen = new_pen

        else:
            alpha = self.config.SMOOTHING_FACTOR

            self.previous_pen = (
                int((1 - alpha) * x + alpha * self.previous_pen[0]),
                int((1 - alpha) * y + alpha * self.previous_pen[1])
            )

        return self.previous_pen
