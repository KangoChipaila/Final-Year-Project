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

        grayscale_feed = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        barcodes = decode(grayscale_feed)

        for barcode in barcodes:

            (x, y, w, h) = barcode.rect
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            barcode_data = barcode.data.decode("utf-8")
            barcode_type = barcode.type

            info = f"Barcode Type: {barcode_type}, Data: {barcode_data}"
            cv2.putText(frame, info, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            if (barcode_data != "" and barcode_type != ""):

                print(info)    
                exit()

        cv2.imshow("Live Camera Feed (NOTE: Press 'q' to Quit)", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    camera_capture.release()
    cv2.destroyAllWindows()

barcode_scanner()