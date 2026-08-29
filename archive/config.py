import numpy as np


class Config:
    # Camera
    CAMERA_INDEX = 0
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 480

    # If camera appears mirrored incorrectly change this
    FLIP_CAMERA = True

    # Pottu target
    TARGET_TOLERANCE = 25

    # Move target slightly above the point between eyebrows
    FOREHEAD_OFFSET_Y = 15

    # Smoothing
    SMOOTHING_FACTOR = 0.6

    # Red marker detection
    RED_LOWER_1 = np.array([0, 180, 100])
    RED_UPPER_1 = np.array([7, 255, 255])

    RED_LOWER_2 = np.array([173, 180, 100])
    RED_UPPER_2 = np.array([180, 255, 255])

    # Marker-tip contour limits at 640x480. PenTracker scales these values
    # automatically if the camera resolution changes.
    PEN_MIN_AREA = 20
    PEN_MAX_AREA = 700
    PEN_MAX_DIMENSION = 50
    PEN_MAX_JUMP = 80
    PEN_REACQUIRE_FRAMES = 5

    # Minimum difference before saying direction
    DIRECTION_THRESHOLD = 15

    # Seconds between repeating voice instruction
    AUDIO_COOLDOWN = 1.2
