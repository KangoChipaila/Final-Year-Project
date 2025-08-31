import cv2
from pyzbar.pyzbar import decode

def barcode_scanner():

    camera_capture = cv2.VideoCapture(0)

    if not camera_capture.isOpened():
        print("Error: Camera failed to open")
        exit()

    while True:

        ret, frame = camera_capture.read()

        if not ret:
            print("Error: Failed to read camera frame")
            break

        cv2.imshow("Live Camera Feed (NOTE: Press 'q' to Quit)", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    camera_capture.release()
    cv2.destroyAllWindows()

barcode_scanner()