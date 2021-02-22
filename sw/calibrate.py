# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Calibration Controller

Performs calibration for hue, center of camera position, and servo offsets
"""

import os
import time
import json
import argparse
import numpy as np
import logging as log

from env import MoabEnv
from common import Vector2
from controllers import pid_controller
from hat import Hat, Icon, Text, plate_angles_to_servo_positions


def ball_close_enough(x, y, radius, max_ball_dist=0.2, min_ball_dist=0.05):
    # reject balls which are too far from the center and too small
    return (
        np.abs(x) < max_ball_dist
        and np.abs(y) < max_ball_dist
        and radius > min_ball_dist
    )


def calibrate_hue(camera_fn, detector_fn, hue_low=0, hue_high=255, hue_steps=20):
    img_frame, elapsed_time = camera_fn()
    hue_options = list(np.linspace(hue_low, hue_high, hue_steps))

    detected_hues = []
    for hue in hue_options:
        ball_detected, ((x, y), radius) = detector_fn(img_frame, hue=hue)
        print(
            f"hue={hue}, ball_detected={ball_detected}, "
            f"(x, y)={(x, y)}, radius={radius}"
        )

        # If we found a ball roughly in the center that is large enough
        if ball_detected and ball_close_enough(x, y, radius):
            detected_hues.append(hue)

    if len(detected_hues) > 0:
        max_hue = max(detected_hues)
        min_hue = min(detected_hues)
        avg_hue = int((max_hue + min_hue) / 2)

        log.info(f"Hue range: [{min_hue} .. {max_hue}]")
        print(f"Hues are: {detected_hues}")
        log.info(f"Hue calibrated: {avg_hue}")

        success = True
        return avg_hue, success

    else:
        log.warning(f"Hue calibration failed.")

        hue = 22  # Reasonable default
        success = False
        return hue, success


def calibrate_pos(camera_fn, detector_fn):
    for i in range(20):  # Try and detect for 20 frames before giving up
        img_frame, elapsed_time = camera_fn()
        ball_detected, ((x, y), radius) = detector_fn(img_frame)

        # If we found a ball roughly in the center that is large enough
        if ball_detected and ball_close_enough(x, y, radius):
            success = True

            x_offset = round(x, 3)
            y_offset = round(y, 3)

            log.info(f"Offset calibrated: [{x_offset:.3f}, {y_offset:.3f}]")
            return (x_offset, y_offset), success

    log.warning(f"Offset calibration failed.")
    success = False
    return (0.0, 0.0), success


def calibrate_servo_offsets(pid_fn, env, stationary_vel=0.001, time_limit=20):
    start_time = time.time()
    action = Vector2(0, 0)

    # Initial high vel_history (to use the vel_hist[-100:] later)
    vel_x_hist = [1.0 for _ in range(100)]
    vel_y_hist = [1.0 for _ in range(100)]

    # Run until the ball has stabilized or the time limit was reached
    while time.time() < start_time + time_limit:
        state = env.step(action)
        action, info = pid_fn(state)

        (x, y, vel_x, vel_y, sum_x, sum_y), ball_detected, buttons = state
        if ball_detected:
            vel_x_hist.append(vel_x)
            vel_y_hist.append(vel_y)
            prev_100_x = np.mean(vel_x_hist[-100:])
            prev_100_y = np.mean(vel_y_hist[-100:])

            # If the average velocity for the last 100 timesteps is under the limit
            if (prev_100_x < stationary_vel) and (prev_100_y < stationary_vel):
                success = True

                # Calculate offsets by calculating servo positions at the
                # current stable position and subtracting the `default` zeroed
                # position of the servos.
                servos = np.array(plate_angles_to_servo_positions(*action))
                servos_zeroed = np.array(plate_angles_to_servo_positions(0, 0))
                servo_offsets = list(servos - servos_zeroed)

                return servo_offsets, success

    # If the plate could be stabilized in time_limit seconds, quit
    log.warning(f"Servo calibration failed.")
    success = False
    return [0.0, 0.0, 0.0], success


def write_calibration(calibration_dict, calibration_file="bot.json"):
    log.info("Writing calibration.")

    # write out stuff
    with open(calibration_file, "w+") as outfile:
        log.info(f"Creating calibration file {calibration_file}")
        json.dump(calibration_dict, outfile, indent=4, sort_keys=True)


def wait_for_joystick(
    hat,
    text_1="Put ball in\ncenter using\nclear stand.",
    text_2="Click joystick\nto continue.",
):
    tick = 0

    # Hacky solution to the bug with too long strings
    def next_text(every_seconds=1):
        """
        This function switches between printing 2 texts to hat after `every`
        timesteps. This is a hacky solution to problems with a memory leak in
        scrolling text in the firmware.
        """
        nonlocal tick
        every = int(every_seconds * 30)
        if (tick % (2 * every)) / (2 * every) == 0:
            hat.print_arbitrary_string(text_1)
        if (tick % (2 * every)) / (2 * every) == 0.5:
            hat.print_arbitrary_string(text_2)
        tick += 1

    while True:
        hat.noop()  # Force new transfer to have up to date button reading
        menu_btn, joy_btn, joy_x, joy_y = hat.poll_buttons()
        next_text()
        time.sleep(1 / 30)
        if joy_btn:
            return


def run_calibrate_pos(env, pid_fn):
    # Get some hidden things from env
    hat = env.hat
    camera_fn = env.camera
    detector_fn = env.detector

    # Calibrate position
    wait_for_joystick(hat, "Put ball in center\nusing clear stand.")
    (x_offset, y_offset), success_pos = calibrate_pos(camera_fn, detector_fn)
    print(f"offsets: (x={x_offset}, y={y_offset}), success={success_pos}.")

    # Save calibration
    with open("bot.json", "r") as f:
        calibration_dict = json.load(f)
    calibration_dict["plate_x_offset"] = x_offset
    calibration_dict["plate_y_offset"] = y_offset
    write_calibration(calibration_dict)

    # Print to display
    if success_pos:
        wait_for_joystick(
            hat,
            f"Offsets =\n{x_offset:.2f, y_offset:.2f}",
            "Click joystick\nto quit...",
        )
    else:
        wait_for_joystick(
            hat,
            "Calibration failed.",
            "Click joystick\nto quit...",
        )


def run_calibrate_hue(env, pid_fn):
    # Get some hidden things from env
    hat = env.hat
    camera_fn = env.camera
    detector_fn = env.detector

    # Calibrate hue
    wait_for_joystick(hat, "Put ball in center\nusing clear stand.")
    hue, success_hue = calibrate_hue(camera_fn, detector_fn)

    # Save calibration
    with open("bot.json", "r") as f:
        calibration_dict = json.load(f)
    calibration_dict["ball_hue"] = hue
    write_calibration(calibration_dict)

    # Print to display
    if success_hue:
        wait_for_joystick(
            hat,
            f"Ball hue = {hue}",
            "Click joystick\nto quit...",
        )
    else:
        wait_for_joystick(
            hat,
            "Calibration failed.",
            "Click joystick\nto quit...",
        )


def run_calibrate_servos(env, pid_fn):
    # Get some hidden things from env
    hat = env.hat
    camera_fn = env.camera
    detector_fn = env.detector

    # Calibrate servo offsets
    wait_for_joystick(hat, "Put ball in center\nwithout clear stand.")
    hat.print_arbitrary_string("Running auto-\ncalibrate servos...")
    servo_offsets, success_offsets = calibrate_servo_offsets(pid_fn, env)

    # Save calibration
    with open("bot.json", "r") as f:
        calibration_dict = json.load(f)
    calibration_dict["servo_offsets"] = servo_offsets
    write_calibration(calibration_dict)

    # Print to display
    if success_offsets:
        o1, o2, o3 = servo_offsets
        wait_for_joystick(
            hat,
            f"offsets = \n({o1:.2f}, {o2:.2f}, {o3:.2f})",
            "Click joystick\nto quit...",
        )
    else:
        wait_for_joystick(
            hat,
            "Calibrate servos failed.",
            "Click joystick\nto quit...",
        )


def main(calibrate_fn, frequency=30, debug=True):
    pid_fn = pid_controller(frequency=frequency)

    with MoabEnv(frequency=frequency, debug=debug) as env:
        state = env.reset(Icon.DOT, Text.CAL)
        calibrate_fn(env, pid_fn)


if __name__ == "__main__":  # Parse command line args
    opts = {
        "position": run_calibrate_pos,
        "hue": run_calibrate_hue,
        "servos": run_calibrate_servos,
    }

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "calibration_type",
        default="hue",
        choices=list(opts.keys()),
        help=f"""Select which calibration to do.
        Options are: {opts.keys()}
        """,
    )
    args, _ = parser.parse_known_args()
    main(opts[args.calibration_type])
