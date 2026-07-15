from setuptools import setup

package_name = "mlf_coin_teleop"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/teleop.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="medkar",
    maintainer_email="mehdi.karim43@gmail.com",
    description="Téléop TurtleBot3 : le palet détecté agit comme un joystick (UDP -> /cmd_vel).",
    license="MPL-2.0",
    entry_points={
        "console_scripts": [
            "joystick_teleop = mlf_coin_teleop.joystick_teleop_node:main",
        ],
    },
)
