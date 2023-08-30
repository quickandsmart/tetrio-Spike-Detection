import cv2 as cv
import numpy as np
from time import time, sleep
from windowcapture import WindowCapture
import threading
import queue
import easyocr
import os


def main_loop(ocr_queue):
    loop_time = time()
    check = 0

    while True:

        screenshot = wincap.get_screenshot()
        image = screenshot.copy()

        # Filter some colors from image to avoid some text from being wrongly recognized
        color_lo = np.array([0, 50, 50])
        color_hi = np.array([210, 255, 255])
        mask = cv.inRange(image, color_lo, color_hi)

        image[mask > 0] = (0, 0, 0)

        # Pre-processing the video to more easily find contours of the spike counter
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        blurred = cv.GaussianBlur(gray, (11, 11), 0)
        thresh = cv.threshold(blurred, 200, 255, cv.THRESH_BINARY)[1]
        erode = cv.erode(thresh, None, iterations=1)
        final_image = cv.dilate(erode, None, iterations=3)

        # Finding all the contours in the image and filtering them, so it's primarily only the ones I want to OCR
        contours, _ = cv.findContours(final_image.copy(), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        large_contours = [contour for contour in contours if is_number_contour(contour, min_area=350)]
        merged_contours = merge_contours(large_contours)
        filtered_contours = [contour for contour in merged_contours if is_number_contour(contour)]
        contour_image = screenshot.copy()
        for contour in merged_contours:
            x, y, w, h = cv.boundingRect(contour)
            cv.rectangle(contour_image, (x, y), (x + w, y + h), (0, 255, 0), 2)
        for contour in filtered_contours:
            x, y, w, h = cv.boundingRect(contour)
            cv.rectangle(contour_image, (x, y), (x + w, y + h), (0, 0, 255), 2)

        # As OCRing takes a while, I only OCR frames that contained the contours I filtered for
        if len(filtered_contours) > 0:
            if check == 0:  # Done to avoid OCRing two back to back frames that are essentially identical
                for contour in filtered_contours:
                    x, y, w, h = cv.boundingRect(contour)
                    # Finding which player created this spike to avoid spike values being improperly calculated
                    if (x + w) > image.shape[1] / 2:
                        side = "right"
                    else:
                        side = "left"
                    # I replaced all the parts in the image not within the contour rectangle with black, to avoid OCRing
                    # parts of the image that aren't important
                    ocr_image = replace_non_contour_with_black(final_image.copy(), contour)
                    ocr_queue.put((ocr_image, loop_time, side, screenshot))
                check = 1
        else:
            check = 0

        # Display the last spike found that sent over 20 lines
        # if not spike_queue.empty():
        #     spike_image = spike_queue.get()
        #     cv.imshow('Last 20+ Spike', spike_image)

        # Display different images to help with bug testing
        # cv.imshow('Video with Contours', contour_image)
        # cv.imshow('Original Video', screenshot.copy())
        # cv.imshow('Pre-processed Video', final_image)
        loop_time = time()

        # End the program
        if cv.waitKey(1) == ord('q'):
            cv.destroyAllWindows()
            exit()


def ocr_worker(ocr_queue, spike_queue):
    current_spike = 0
    current_time = 0
    current_side = "neither"
    original_image = None
    text = None
    current_directory = os.getcwd()
    new_directory = os.path.join(current_directory, "TC15 Spikes")
    os.chdir(new_directory)
    while True:
        if ocr_queue.empty():
            # The action of queue.put() is asyncronous causing issues with the queue.empty() command
            # so the thread is paused temporarily to avoid this
            sleep(1)
        else:

            output = ocr_queue.get()
            preprocessed_image, current_time, current_side, original_image = output
            result = reader.readtext(preprocessed_image, batch_size=2, decoder='greedy', blocklist=[' '],
                                     text_threshold=.6, rotation_info=[90, 180, 270])
            for text in result:
                # Only accepting the results that are numeric with a length of 2 (in the range of 10-99)
                # and the accuracy is greater than 70%
                if len(text[1]) == 2 and text[1].isnumeric() and text[2] > 0.70:
                    current_spike = text[1]
            ocr_queue.task_done()

        # Only current spikes that are within 20-49 range are read. Spikes greater than 50 are very unlikely to occur
        # in game and are often more likely a mistake by the OCR which is why they are not checked
        if 20 <= int(current_spike) < 50:
            # I wait 1 second if the queue is empty to make sure if the spike wasn't finished, the queue wouldn't be
            # empty
            if ocr_queue.empty():
                sleep(1)
            if ocr_queue.empty():
                # If the queue is empty that either means the spike is finished or the next part of the spike has not
                # occured yet. We only want to OCR spikes that have finished, and since the spike timer is 1 second
                # before it resets, we only check images where the spike timer has reset, aka the spike is done
                if current_time + 1 < time():
                    spike_image = cv.rectangle(original_image.copy(), pt1=(int(text[0][0][0]), int(text[0][0][1])),
                                               pt2=(int(text[0][2][0]), int(text[0][2][1])), color=(255, 0, 0),
                                               thickness=3)
                    spike_image = cv.putText(spike_image, current_spike, (int(text[0][2][0]), int(text[0][2][1])),
                                             cv.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1, cv.LINE_AA)
                    print(current_side.capitalize() + " Player sent a " + current_spike + " spike")
                    # Displaying the image in this thread didn't seem to work which is why a queue was used to display
                    # the image in the main loop
                    # spike_queue.put(spike_image)
                    filename = str(time()) + "_" + str(current_spike) + "_spike_" + str(current_side) + ".png"
                    cv.imwrite(filename, spike_image)
                    current_spike = 0

            else:
                # If the queue is not empty that means the spike might not be finished, so I checked if there was
                # another image in the queue that is a part of the current spike
                next_side = "neither"
                next_time = 0
                counter = 0
                temp_queue = queue.Queue()
                while not ocr_queue.empty():
                    output = ocr_queue.get(timeout=1)
                    _, other_time, side, next_image = output
                    if side == current_side and counter == 0:
                        next_side = side
                        next_time = other_time
                        counter = 1
                    temp_queue.put(output)
                while not temp_queue.empty():
                    ocr_queue.put(temp_queue.get())
                if (next_side == current_side and current_time + 1 < next_time) or \
                        (next_side != current_side and current_time + 1 < time()):
                    spike_image = cv.rectangle(original_image.copy(), pt1=(int(text[0][0][0]), int(text[0][0][1])),
                                               pt2=(int(text[0][2][0]), int(text[0][2][1])), color=(255, 0, 0),
                                               thickness=3)
                    spike_image = cv.putText(spike_image, current_spike, (int(text[0][2][0]), int(text[0][2][1])),
                                             cv.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1, cv.LINE_AA)
                    print(current_side.capitalize() + " Player sent a " + current_spike + " spike")
                    # spike_queue.put(spike_image)
                    filename = str(time()) + "_" + str(current_spike) + "_spike_" + str(current_side) + ".png"
                    cv.imwrite(filename, spike_image)
                    current_spike = 0


def is_number_contour(contour, aspect_ratio_range=(0.1, 1.7), min_area=2000):
    x, y, w, h = cv.boundingRect(contour)
    aspect_ratio = w / float(h)
    area = cv.contourArea(contour)
    return aspect_ratio_range[0] <= aspect_ratio <= aspect_ratio_range[1] and min_area <= area


def merge_contours(contours, merge_threshold=70):
    merged_contours = []
    used_contours = [False] * len(contours)

    for i, contour1 in enumerate(contours):
        if not used_contours[i]:
            merged_contour = contour1
            x1, y1, w1, h1 = cv.boundingRect(contour1)
            center1 = (x1 + w1 // 2, y1 + h1 // 2)

            for j, contour2 in enumerate(contours[i + 1:], start=i + 1):
                if not used_contours[j]:
                    x2, y2, w2, h2 = cv.boundingRect(contour2)
                    center2 = (x2 + w2 // 2, y2 + h2 // 2)

                    distance = ((center1[0] - center2[0]) ** 2 + (center1[1] - center2[1]) ** 2) ** 0.5
                    if distance <= merge_threshold:
                        merged_contour = cv.convexHull(np.vstack((merged_contour, contour2)))
                        used_contours[j] = True

            merged_contours.append(merged_contour)

    return merged_contours


def replace_non_contour_with_black(image, contour):
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    x, y, w, h = cv.boundingRect(contour)
    cv.rectangle(mask, (x, y), (x + w, y + h), 255, -1)
    mask_inv = cv.bitwise_not(mask)
    extracted_text = cv.bitwise_and(image, image, mask=mask)
    black_image = np.zeros(image.shape, dtype=np.uint8)
    blacked_out_image = cv.bitwise_and(black_image, black_image, mask=mask_inv)
    result_image = cv.add(extracted_text, blacked_out_image)

    return result_image


if __name__ == "__main__":
    # if gpu is not available it can be disabled, however it will take longer for the image to be read
    reader = easyocr.Reader(['en'], gpu=True)

    # The window you want to record the screenshots of
    # Credits to Learn Code by Gaming for the Windowcapture.py file
    # and his playlist for object detection https://www.youtube.com/playlist?list=PL1m2M8LQlzfKtkKq2lK5xko4X-8EZzFPI
    # To find the exact name of the window you want to capture you can run the commands below
    # WindowCapture.list_window_names()
    # exit()
    wincap = WindowCapture(
        'Highlight: TETR.IO Cup 15 - ft. CZSmall, Diao, FireStorm, Blaarg, VinceHD, Ajanba - Twitch - Google Chrome'
    )

    # Queues used to transfer values from the two threads
    ocr_queue = queue.Queue()
    spike_queue = queue.Queue()

    # Since the process of OCRing takes a long time, I created a seperate thread where the OCRing process will take
    # place to allow the two actions to run in parrallel.
    ocr_thread = threading.Thread(target=ocr_worker, args=(ocr_queue, spike_queue,))
    ocr_thread.start()

    main_loop(ocr_queue)
