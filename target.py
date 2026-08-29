class TargetDetector:

    def __init__(self, config):
        self.config = config
        self.previous_target = None

    def get_target(self, face_point, frame_shape):

        if face_point is None:
            return None

        height, width, _ = frame_shape

        point_x, point_y = face_point

        x = int(point_x * width)

        y = int(point_y * height)

        # Move slightly upward
        y -= self.config.FOREHEAD_OFFSET_Y

        new_target = (x, y)

        # Smooth movement
        if self.previous_target is None:
            self.previous_target = new_target

        else:
            alpha = self.config.SMOOTHING_FACTOR

            smooth_x = int(
                (1 - alpha) * x
                + alpha * self.previous_target[0]
            )

            smooth_y = int(
                (1 - alpha) * y
                + alpha * self.previous_target[1]
            )

            self.previous_target = (
                smooth_x,
                smooth_y
            )

        return self.previous_target
