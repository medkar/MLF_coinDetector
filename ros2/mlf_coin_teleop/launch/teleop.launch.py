# SPDX-License-Identifier: MPL-2.0
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="mlf_coin_teleop",
            executable="joystick_teleop",
            name="mlf_coin_joystick_teleop",
            output="screen",
            parameters=[{
                "udp_port": 5005,
                "max_linear": 0.15,     # m/s
                "max_angular": 0.8,     # rad/s
                "deadzone": 0.12,
                "timeout": 0.5,
                "publish_rate": 20.0,
                "cmd_vel_topic": "/cmd_vel",
            }],
        ),
    ])
