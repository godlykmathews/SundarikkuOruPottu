import os
import threading
import time
import cv2
from gtts import gTTS
import numpy as np
import serial

last_speech_time = 0
speech_cooldown = 2.5  # Seconds between spoken instructions
is_speaking = False

def speak_gtts(text):
    """Generate and play Google TTS audio using native Windows Media Player COM API."""
    global is_speaking
    is_speaking = True
    temp_mp3 = os.path.abspath("temp_guidance.mp3")
    try:
        # 1. Save gTTS to MP3
        tts = gTTS(text=text, lang='en')
        tts.save(temp_mp3)
        
        # 2. Use Windows Media Player COM object to play MP3 natively
        ps_cmd = (
            f'$player = New-Object -ComObject WMPlayer.OCX; '
            f'$player.URL = "{temp_mp3}"; '
            f'$player.controls.play(); '
            f'while ($player.playState -ne 1) {{ Start-Sleep -Milliseconds 100 }}'
        )
        os.system(f'powershell -c "{ps_cmd}" > NUL 2>&1')

    except Exception as e:
        print(f"gTTS Error: {e}")
    finally:
        # Clean up temporary audio files
        if os.path.exists(temp_mp3):
            try:
                os.remove(temp_mp3)
            except Exception:
                pass
        is_speaking = False

# Initialize Serial Connection to ESP32
# Replace 'COM3' with your actual ESP32 COM port
try:
    ser = serial.Serial('COM3', 115200, timeout=1)
    time.sleep(2)  # Give ESP32 time to reset after opening connection
    print("Serial connected to ESP32!")
except Exception as e:
    print(f"Serial connection error: {e}")
    ser = None

xml_filename = "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(xml_filename)

if face_cascade.empty():
    raise IOError(f"Could not load Haar Cascade XML from file: {xml_filename}")

cap = cv2.VideoCapture(1)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        print("Unable to fetch webcam frame.")
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )

    forehead_x = None
    forehead_y = None

    for x, y, w, h in faces:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)

        # Calculate forehead coordinates (centered horizontally, ~30% from top of face box)
        forehead_x = int(x + (w / 2))
        forehead_y = int(y + (h * 0.3))

        # Draw a small filled red circle on the forehead
        cv2.circle(frame, (forehead_x, forehead_y), 10, (0, 0, 255), -1)

        # Calculate face center target coordinates
        center_x = float(x + (w // 2))
        center_y = float(y + (h // 2))

        # Print face coordinates to terminal
        print(f"Face Coordinates: X = {center_x:.1f}, Y = {center_y:.1f}")

        # Send space-separated "X Y\n" format expected by parseFloat()
        if ser and ser.is_open:
            data_string = f"{center_x:.1f} {center_y:.1f}\n"
            ser.write(data_string.encode('utf-8'))

    # Detect red object (marker cap) using HSV color thresholding
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Lower and upper range for red color in HSV space
    lower_red1 = np.array([0, 120, 70])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 120, 70])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv_frame, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv_frame, lower_red2, upper_red2)
    red_mask = mask1 + mask2

    # Find contours of the red object
    contours, _ = cv2.findContours(red_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    red_center_x = None
    red_center_y = None

    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 500:  # Minimum area threshold to ignore noise
            M = cv2.moments(contour)
            if M["m00"] != 0:
                red_center_x = int(M["m10"] / M["m00"])
                red_center_y = int(M["m01"] / M["m00"])

                # Draw a small green dot on the center of the red object
                cv2.circle(frame, (red_center_x, red_center_y), 10, (0, 255, 0), -1)
                print(f"Red Object Coordinates: X = {red_center_x}, Y = {red_center_y}")
                break

    # Calculate difference between green spot (hand) and red spot (forehead) to guide user
    if forehead_x is not None and red_center_x is not None:
        dx = forehead_x - red_center_x
        dy = forehead_y - red_center_y
        distance = int(np.sqrt(dx**2 + dy**2))

        # Draw a line between the two points
        cv2.line(frame, (red_center_x, red_center_y), (forehead_x, forehead_y), (255, 255, 0), 2)

        # Build movement directions
        directions = []
        if abs(dx) > 20:
            directions.append("RIGHT" if dx > 0 else "LEFT")
        if abs(dy) > 20:
            directions.append("DOWN" if dy > 0 else "UP")

        if distance < 30:
            guide_text = "TARGET REACHED!"
            speech_text = "Target reached"
            color = (0, 255, 0)
        else:
            guide_text = f"Move Hand: {' + '.join(directions)}"
            speech_text = f"Move {' and '.join(directions)}"
            color = (0, 255, 255)

        cv2.putText(frame, guide_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        print(f"Distance: {distance}px | Guidance: {guide_text}")

        # Trigger Google TTS spoken instructions periodically in background thread
        current_time = time.time()
        if (current_time - last_speech_time > speech_cooldown) and not is_speaking:
            last_speech_time = current_time
            threading.Thread(target=speak_gtts, args=(speech_text,), daemon=True).start()

    cv2.imshow("Haar Cascade Face Detection", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
if ser and ser.is_open:
    ser.close()