import cv2
import os
import queue
import threading
import platform
# ----------------------------------------------------------------------------------------------------------------------
class utils_video_saver:
    def __init__(self, folder_output, fps=20, width=None, height=None):
        self.folder_output = folder_output
        self.video_path = os.path.join(self.folder_output, "video.mp4")
        self.temp_path = os.path.join(self.folder_output, "video_large.avi")
        self.fps = fps
        self.width = width
        self.height = height
        self.writer = None
        self.writer_initialized = False
        self.fourcc = cv2.VideoWriter_fourcc(*'MJPG')

        self.q = queue.Queue(maxsize=250)
        self.frame_count = 0
        self.skipped_frames = 0
        self.stop_event = threading.Event()
        threading.Thread(target=self.thread_video_saver).start()

        if self.width is not None and self.height is not None:
            self.initialize_writer(self.width, self.height)

    # ----------------------------------------------------------------------------------------------------------------------
    def initialize_writer(self, width, height):
        if self.writer_initialized:
            return
        self.width = width
        self.height = height
        self.writer = cv2.VideoWriter(self.temp_path, self.fourcc, self.fps, (self.width, self.height))
        self.writer_initialized = True
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def thread_video_saver(self):
        try:
            while not (self.stop_event.is_set() and self.q.empty()):
                try:
                    frame = self.q.get(timeout=0.1)
                except queue.Empty:
                    continue

                if not self.writer_initialized:
                    if len(frame.shape) == 3:
                        height, width = frame.shape[:2]
                    else:
                        height, width = frame.shape
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    self.initialize_writer(width, height)

                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                if frame.shape[:2] != (self.height, self.width):
                    frame = cv2.resize(frame, (self.width, self.height))

                if self.writer is not None and self.writer.isOpened():
                    self.writer.write(frame)
                    self.frame_count += 1

                self.q.task_done()
        except Exception as e:
            print(f"Error in worker thread: {e}")
        finally:
            if self.writer is not None and self.writer.isOpened():
                self.writer.release()

    # ----------------------------------------------------------------------------------------------------------------------
    def append_frame(self, frame):
        if frame is None:
            return
        try:
            self.q.put(frame, block=True, timeout=None)
        except queue.Full:
            print("Warning: queue full, dropping frame")

    # ----------------------------------------------------------------------------------------------------------------------
    def convert_video(self):
        if not os.path.isfile(self.temp_path):
            return

        print(f"Converting video ...")
        cmd_mid_compression = f"ffmpeg -i {self.temp_path} -c:v libx264 -crf 31 -preset ultrafast -y {self.video_path} 2>/dev/null"
        exit_code = os.system(cmd_mid_compression)

        if exit_code == 0 and os.path.exists(self.video_path):
            os.remove(self.temp_path)
        else:
            if os.path.isfile(self.temp_path):
                os.rename(self.temp_path, self.video_path.replace('.mp4', '.avi'))
        print(f"Converted")
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def save_captured_video(self):
        self.stop()
        self.q.join()

        if self.writer is not None and self.writer.isOpened():
            self.writer.release()

        if not os.path.exists(self.temp_path):
            print("Error: temp file not created")
            return

        if platform.system() != "Windows":
            pass
            #self.convert_video()
        else:
            os.rename(self.temp_path, self.video_path)

        return

    # ----------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stop_event.set()
        return
    # ----------------------------------------------------------------------------------------------------------------------
