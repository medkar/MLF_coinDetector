# mlf_coin_teleop — téléop TurtleBot3 « palet = joystick »

Nœud ROS2 qui pilote un **TurtleBot3 Waffle** à partir de la position du palet détecté par
l'app UNO Q. Le palet agit comme un **joystick** : sa position par rapport au centre de la
mire donne l'avance et la rotation du robot.

```
UNO Q (App Lab)  ──UDP {jx,jy}──►  nœud joystick_teleop  ──/cmd_vel (Twist)──►  Waffle
```

- `jy > 0` (palet vers le haut)   → `linear.x > 0`  (le robot avance)
- `jx > 0` (palet vers la droite) → `angular.z < 0` (le robot tourne à droite)
- Le Waffle est **différentiel** : la « diagonale » = avancer **+** tourner (trajectoire courbe).
- **Sécurité** : plus de paquet UDP depuis `timeout` s (palet perdu) → `Twist` nul → le robot s'arrête.

## Où faire tourner ce nœud

Sur une machine ROS2 (le Raspberry Pi du Waffle, ou un PC ROS2) **sur le même réseau** que le
robot, avec le **même `ROS_DOMAIN_ID`** que le Waffle (sinon ils ne se voient pas en DDS).

> Le nœud n'a besoin **que** de `rclpy` + `geometry_msgs` — pas des drivers TurtleBot3.
> Il publie sur `/cmd_vel`, que le robot écoute déjà via son bringup.

## Build

Copie le dossier `mlf_coin_teleop/` dans le `src/` d'un workspace ROS2, puis :

```bash
cd ~/ros2_ws
colcon build --packages-select mlf_coin_teleop
source install/setup.bash
```

## Lancer

```bash
# adapte le port si besoin ; par défaut il écoute l'UDP sur 5005
ros2 launch mlf_coin_teleop teleop.launch.py
# ou directement :
ros2 run mlf_coin_teleop joystick_teleop --ros-args -p max_linear:=0.15 -p max_angular:=0.8
```

Vérifier que ça bouge :
```bash
ros2 topic echo /cmd_vel
```

## Côté UNO Q

Dans `python/main.py` (branche `ros2-waffle-teleop`), règle en tête de fichier :
```python
ROS_ENABLED = True
WAFFLE_HOST = "192.168.x.x"   # IP de la machine qui fait tourner CE nœud
WAFFLE_PORT = 5005
```
L'app envoie alors `{jx, jy}` en UDP à chaque détection de palet.

## Paramètres du nœud

| Paramètre | Défaut | Rôle |
|---|---|---|
| `udp_port` | 5005 | port UDP d'écoute |
| `max_linear` | 0.15 | vitesse d'avance max (m/s) |
| `max_angular` | 0.8 | vitesse de rotation max (rad/s) |
| `deadzone` | 0.12 | zone morte autour du centre (palet ~centré = stop) |
| `timeout` | 0.5 | s sans paquet avant arrêt de sécurité |
| `cmd_vel_topic` | `/cmd_vel` | topic de commande |

⚠️ Commence avec des vitesses **basses** (valeurs par défaut) et le robot **surélevé / roues en l'air**
pour vérifier le sens avant de le poser au sol.
