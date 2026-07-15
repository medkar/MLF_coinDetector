# SPDX-License-Identifier: MPL-2.0
"""Nœud ROS2 : le palet détecté (envoyé en UDP par l'UNO Q) agit comme un joystick.

Reçoit {jx, jy} (chacun dans [-1, 1]) :
  - jy > 0  -> le palet est vers le haut  -> le robot AVANCE   (linear.x > 0)
  - jx > 0  -> le palet est vers la droite -> le robot TOURNE À DROITE (angular.z < 0)

Le Waffle est différentiel : "diagonale" = avancer + tourner (courbe).
Sécurité : si aucun paquet reçu depuis `timeout` secondes, on publie un Twist nul (stop).
"""

import json
import socket

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class JoystickTeleop(Node):
    def __init__(self):
        super().__init__("mlf_coin_joystick_teleop")

        # --- Paramètres (surchargeables au lancement / dans le launch file) ---
        self.declare_parameter("udp_port", 5005)
        self.declare_parameter("max_linear", 0.15)      # m/s (vitesse d'avance max)
        self.declare_parameter("max_angular", 0.8)      # rad/s (vitesse de rotation max)
        self.declare_parameter("deadzone", 0.12)        # zone morte joystick [0-1]
        self.declare_parameter("timeout", 0.5)          # s sans paquet -> stop
        self.declare_parameter("publish_rate", 20.0)    # Hz
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        self.max_linear = float(self.get_parameter("max_linear").value)
        self.max_angular = float(self.get_parameter("max_angular").value)
        self.deadzone = float(self.get_parameter("deadzone").value)
        self.timeout = float(self.get_parameter("timeout").value)
        port = int(self.get_parameter("udp_port").value)
        topic = str(self.get_parameter("cmd_vel_topic").value)

        self.pub = self.create_publisher(Twist, topic, 10)

        # Socket UDP non bloquant
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.setblocking(False)

        self.jx = 0.0
        self.jy = 0.0
        self.last_rx = self.get_clock().now()

        rate = float(self.get_parameter("publish_rate").value)
        self.timer = self.create_timer(1.0 / rate, self._on_timer)
        self.get_logger().info(f"Écoute joystick UDP sur :{port} -> publie {topic} "
                               f"(max_lin={self.max_linear} m/s, max_ang={self.max_angular} rad/s)")

    def _drain_socket(self):
        """Lit tous les paquets en attente, ne garde que le dernier."""
        latest = None
        while True:
            try:
                data, _ = self.sock.recvfrom(1024)
                latest = data
            except BlockingIOError:
                break
            except OSError as e:
                self.get_logger().warn(f"UDP recv: {e}")
                break
        if latest is not None:
            try:
                msg = json.loads(latest.decode("utf-8"))
                self.jx = float(msg.get("jx", 0.0))
                self.jy = float(msg.get("jy", 0.0))
                self.last_rx = self.get_clock().now()
            except (ValueError, TypeError) as e:
                self.get_logger().warn(f"JSON invalide: {e}")

    def _deadzone(self, v):
        if abs(v) < self.deadzone:
            return 0.0
        # remappe [deadzone, 1] -> [0, 1] pour éviter un saut au sortir de la zone morte
        s = (abs(v) - self.deadzone) / (1.0 - self.deadzone)
        return (1.0 if v > 0 else -1.0) * min(1.0, s)

    def _on_timer(self):
        self._drain_socket()
        twist = Twist()
        age = (self.get_clock().now() - self.last_rx).nanoseconds * 1e-9
        if age <= self.timeout:
            jy = self._deadzone(self.jy)
            jx = self._deadzone(self.jx)
            twist.linear.x = jy * self.max_linear
            twist.angular.z = -jx * self.max_angular   # droite = sens horaire = angular.z négatif
        # sinon : Twist nul -> stop de sécurité (palet perdu / plus de paquets)
        self.pub.publish(twist)


def main(args=None):
    rclpy.init(args=args)
    node = JoystickTeleop()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.pub.publish(Twist())  # stop propre à l'arrêt
        except Exception:
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
