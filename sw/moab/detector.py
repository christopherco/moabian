# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
HSV filtering ball detector
"""

from typing import List, Optional

import cv2
import math
import numpy as np
from pymoab import hue_mask
from common import Vector2, CircleFeature, Calibration


def pixels_to_meters(vec, frame_size=256, feild_of_view=1.05):
    # The plate is default roughly 105% of the field of view
    plate_diameter_meters = 0.225
    plate_diameter_pixels = frame_size * feild_of_view
    conversion = plate_diameter_meters / plate_diameter_pixels

    return vec * conversion


class HSVDetector:
    def __init__(
        self,
        calibration=None,
        frame_size=256,
        kernel_size=[5, 5],
        ball_min=0.06,
        ball_max=0.22,
        debug=False,
        hue=None,  # hue [0..255]
        sigma=0.05,  # narrow: 0.01, wide: 0.1
        bandpass_gain=12.0,
        mask_gain=4.0,
    ):
        if calibration:
            self.calibration = calibration
        else:
            self.calibration = Calibration()

        self.frame_size = frame_size
        self.kernel_size = kernel_size
        self.ball_min = ball_min
        self.ball_max = ball_max
        self.debug = debug
        self.hue = hue
        self.sigma = sigma
        self.bandpass_gain = bandpass_gain
        self.mask_gain = mask_gain
        self.i = 0

        # if we haven't been overridden, use ballHue from calibration
        if self.hue is None:
            self.hue = self.calibration.ball_hue

        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, tuple(kernel_size))

    def __call__(self, img: np.ndarray, debug=False) -> CircleFeature:
        if img is not None:
            # covert to HSV space
            color = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            # 0 == red
            if self.hue is None:
                self.hue = 0

            # run through each triplet and perform our masking filter on it.
            # hue_mask coverts the hsv image into a grayscale image with a
            # bandpass applied centered around hue, with width sigma
            hue_mask(
                color,
                self.hue,
                self.sigma,
                self.bandpass_gain,
                self.mask_gain,
            )

            # convert to b&w mask from grayscale image
            mask = cv2.inRange(
                color, np.array((200, 200, 200)), np.array((255, 255, 255))
            )

            # expand b&w image with a dialation filter
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)

            contours = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )[-2]

            ball_detected = False
            if len(contours) > 0:
                contour_peak = max(contours, key=cv2.contourArea)
                ((self.x_obs, self.y_obs), radius) = cv2.minEnclosingCircle(
                    contour_peak
                )

                # Determine if ball size is the appropriate size
                norm_radius = radius / self.frame_size
                if self.ball_min < norm_radius < self.ball_max:
                    ball_detected = True

                    # Convert from pixels to absolute with 0,0 as center of detected plate
                    x = self.x_obs - self.frame_size // 2
                    y = self.y_obs - self.frame_size // 2
                    center = Vector2(x, y).rotate(np.radians(-30))

                    if debug:
                        # Draw on a circle
                        center = (int(self.x_obs), int(self.y_obs))
                        cv2.circle(img, center, 2, (255, 0, 255), 2)
                        cv2.circle(img, center, int(radius), (255, 0, 255), 2)

                        # Rotate the image -30 degrees so it looks normal
                        w, h = img.shape[:2]
                        center = (w / 2, h / 2)
                        M = cv2.getRotationMatrix2D(center, 30, 1.0)
                        img = cv2.warpAffine(img, M, (w, h))
                        img = img[::-1, :, :]  # Mirror along x axis

                        cv2.imwrite(
                            "/tmp/camera/frame.jpg",
                            img,
                            [cv2.IMWRITE_JPEG_QUALITY, 70],
                        )

                    center = Vector2(x, y).rotate(np.radians(-30))
                    center = pixels_to_meters(center, self.frame_size)
                    print(center)
                    return ball_detected, (center, radius)
                else:
                    pass

        if debug:
            # Rotate the image -30 degrees so it looks normal
            w, h = img.shape[:2]
            center = (w / 2, h / 2)
            M = cv2.getRotationMatrix2D(center, 30, 1.0)
            img = cv2.warpAffine(img, M, (w, h))
            img = img[::-1, :, :]  # Mirror along x axis

            cv2.imwrite(
                "/tmp/camera/frame.jpg",
                img,
                [cv2.IMWRITE_JPEG_QUALITY, 70],
            )

        return ball_detected, (Vector2(0, 0), 0.0)
