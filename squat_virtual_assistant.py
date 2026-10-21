"""Beginner-friendly real-time squat coach.

Controls: q = quit, r = reset repetitions and timer.
This is an educational form guide, not medical advice.
"""

import math
import queue
import threading
import time
import ctypes
from pathlib import Path
from urllib.request import urlretrieve

import cv2
import mediapipe as mp
import pyttsx3
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision


CAMERA_INDEX = 0
STANDING_KNEE_ANGLE = 160
SQUAT_KNEE_ANGLE = 100
KNEES_TOO_FAR_FORWARD_ANGLE = 60
FEEDBACK_DELAY_SECONDS = 3

# MediaPipe Tasks needs this model file in addition to the Python package.
MODEL_FILE = Path(__file__).with_name("pose_landmarker_full.task")
WRONG_POSTURE_SOUND = Path(__file__).with_name("fahhhhh.mp3")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)

# Index numbers of the 33 pose landmarks returned by MediaPipe Tasks.
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28


def download_pose_model_if_needed():
    """Download the official pose model once, only if it is missing."""
    if MODEL_FILE.exists():
        return True

    print("Downloading the MediaPipe pose model. This happens only once...")
    try:
        urlretrieve(MODEL_URL, MODEL_FILE)
        print("Pose model downloaded successfully.")
        return True
    except OSError as error:
        print(f"Could not download the pose model: {error}")
        print("Check your internet connection and run the program again.")
        return False


def calculate_angle(point_a, point_b, point_c):
    """Return the angle ABC in degrees using three (x, y) points."""
    ab_x = point_a.x - point_b.x
    ab_y = point_a.y - point_b.y
    cb_x = point_c.x - point_b.x
    cb_y = point_c.y - point_b.y
    dot_product = ab_x * cb_x + ab_y * cb_y
    magnitude_ab = math.sqrt(ab_x**2 + ab_y**2)
    magnitude_cb = math.sqrt(cb_x**2 + cb_y**2)

    if magnitude_ab == 0 or magnitude_cb == 0:
        return 0

    cosine = dot_product / (magnitude_ab * magnitude_cb)
    cosine = max(-1, min(1, cosine))
    return math.degrees(math.acos(cosine))


class VoiceCoach:
    """Speaks on a background thread so webcam video remains smooth."""

    def __init__(self):
        self.messages = queue.Queue()
        self.last_message = ""
        self.last_spoken_time = 0
        threading.Thread(target=self._speak_messages, daemon=True).start()

    def _speak_messages(self):
        engine = pyttsx3.init()
        engine.setProperty("rate", 165)
        while True:
            message = self.messages.get()
            engine.say(message)
            engine.runAndWait()

    def say(self, message):
        """Queue feedback but avoid repeating it every video frame."""
        now = time.time()
        is_recent_repeat = (
            message == self.last_message
            and now - self.last_spoken_time < FEEDBACK_DELAY_SECONDS
        )
        if not is_recent_repeat:
            self.messages.put(message)
            self.last_message = message
            self.last_spoken_time = now


class WrongPostureSound:
    """Play the supplied MP3 without stopping the webcam video."""

    def __init__(self):
        self.last_played_time = 0

    def play(self):
        """Play the effect at most once every few seconds."""
        now = time.time()
        if now - self.last_played_time < FEEDBACK_DELAY_SECONDS:
            return

        self.last_played_time = now
        threading.Thread(target=self._play_sound, daemon=True).start()

    def _play_sound(self):
        """Use Windows' built-in media player support to play an MP3 file."""
        if not WRONG_POSTURE_SOUND.exists():
            print(f"Sound file not found: {WRONG_POSTURE_SOUND}")
            return

        sound_alias = "wrong_posture_alert"
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW(f"close {sound_alias}", None, 0, None)
        winmm.mciSendStringW(
            f'open "{WRONG_POSTURE_SOUND}" type mpegvideo alias {sound_alias}',
            None,
            0,
            None,
        )
        winmm.mciSendStringW(f"play {sound_alias}", None, 0, None)


def landmark_visibility(landmark):
    """Treat a missing visibility value as zero."""
    return landmark.visibility or 0


def choose_side(landmarks):
    """Use the side of the body that is clearer to the camera."""
    left_hip = landmarks[LEFT_HIP]
    right_hip = landmarks[RIGHT_HIP]

    if landmark_visibility(left_hip) >= landmark_visibility(right_hip):
        return (
            landmarks[LEFT_SHOULDER], left_hip, landmarks[LEFT_KNEE],
            landmarks[LEFT_ANKLE], "left"
        )

    return (
        landmarks[RIGHT_SHOULDER], right_hip, landmarks[RIGHT_KNEE],
        landmarks[RIGHT_ANKLE], "right"
    )


def assess_squat(shoulder, hip, knee, ankle):
    """Return knee angle, hip angle, and one short correction message."""
    knee_angle = calculate_angle(hip, knee, ankle)
    hip_angle = calculate_angle(shoulder, hip, knee)

    if knee_angle < KNEES_TOO_FAR_FORWARD_ANGLE:
        message = "Keep your knees in line with your toes."
        posture_is_wrong = True
    elif hip_angle < 55:
        message = "Keep your chest more upright."
        posture_is_wrong = True
    elif knee_angle <= SQUAT_KNEE_ANGLE:
        message = "Good depth. Push through your heels."
        posture_is_wrong = False
    else:
        message = "Lower slowly into your squat."
        posture_is_wrong = False

    return knee_angle, hip_angle, message, posture_is_wrong


def draw_text(frame, text, position, color=(255, 255, 255), scale=0.7):
    """Draw readable text with a black outline."""
    cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


def draw_squat_landmarks(frame, shoulder, hip, knee, ankle):
    """Draw the four landmarks used in the squat-angle calculation."""
    height, width = frame.shape[:2]
    landmarks = [shoulder, hip, knee, ankle]
    points = [(int(point.x * width), int(point.y * height)) for point in landmarks]

    for first, second in zip(points, points[1:]):
        cv2.line(frame, first, second, (0, 255, 0), 3)
    for point in points:
        cv2.circle(frame, point, 6, (0, 0, 255), -1)


def create_pose_detector():
    """Create one MediaPipe Tasks detector for webcam video frames."""
    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_FILE)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


def main():
    if not download_pose_model_if_needed():
        return

    camera = cv2.VideoCapture(CAMERA_INDEX)
    if not camera.isOpened():
        print("Could not open the camera. Try changing CAMERA_INDEX to 1.")
        return

    coach = VoiceCoach()
    wrong_posture_sound = WrongPostureSound()
    repetitions = 0
    squat_stage = "up"
    workout_start_time = time.time()

    # The Tasks API uses detect_for_video with increasing timestamps.
    with create_pose_detector() as pose_detector:
        while camera.isOpened():
            success, frame = camera.read()
            if not success:
                print("Could not read a video frame.")
                break

            frame = cv2.flip(frame, 1)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            media_pipe_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb_frame,
            )
            timestamp_ms = int(time.monotonic() * 1000)
            detection_result = pose_detector.detect_for_video(
                media_pipe_image,
                timestamp_ms,
            )

            feedback = "Step sideways so your full body is visible."
            feedback_color = (0, 180, 255)

            if detection_result.pose_landmarks:
                landmarks = detection_result.pose_landmarks[0]
                shoulder, hip, knee, ankle, side = choose_side(landmarks)
                knee_angle, hip_angle, feedback, posture_is_wrong = assess_squat(
                    shoulder, hip, knee, ankle
                )

                # Count one repetition only after moving from down to up.
                if knee_angle <= SQUAT_KNEE_ANGLE:
                    squat_stage = "down"
                elif knee_angle >= STANDING_KNEE_ANGLE and squat_stage == "down":
                    squat_stage = "up"
                    repetitions += 1
                    feedback = "Great squat. Rep counted."

                if "Good" in feedback or "Great" in feedback:
                    feedback_color = (0, 255, 0)

                if posture_is_wrong:
                    wrong_posture_sound.play()

                draw_squat_landmarks(frame, shoulder, hip, knee, ankle)
                draw_text(frame, f"{side.title()} knee: {int(knee_angle)} degrees", (15, 105))
                draw_text(frame, f"Hip: {int(hip_angle)} degrees", (15, 135))

            coach.say(feedback)
            elapsed_seconds = int(time.time() - workout_start_time)
            minutes, seconds = divmod(elapsed_seconds, 60)
            draw_text(frame, f"Reps: {repetitions}", (15, 35), (0, 255, 0), 1)
            draw_text(frame, f"Time: {minutes:02}:{seconds:02}", (15, 70), (0, 255, 0), 1)
            draw_text(frame, f"Stage: {squat_stage}", (15, 165))
            draw_text(frame, feedback, (15, frame.shape[0] - 25), feedback_color, 0.6)
            draw_text(frame, "q = quit | r = reset", (15, frame.shape[0] - 55), (200, 200, 200), 0.55)

            cv2.imshow("AI Squat Virtual Assistant", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                repetitions = 0
                squat_stage = "up"
                workout_start_time = time.time()
                coach.say("Counter and timer reset.")

    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
