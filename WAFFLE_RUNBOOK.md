# Runbook — Waffle « palet = joystick »

Guide opérationnel pour démarrer **tout le système** : le palet détecté par l'UNO Q pilote un
TurtleBot3 Waffle comme un joystick.

```
UNO Q (App Lab)  ──UDP {jx,jy} :5005──►  joystick_teleop (sur le Pi)  ──/cmd_vel──►  turtlebot3_bringup ──► OpenCR ──► roues
```

Le nœud `mlf_coin_teleop` tourne **directement sur le Raspberry Pi du Waffle** (il fait aussi le bringup).
Voir [ros2/README.md](ros2/README.md) pour le détail du package et ses paramètres.

---

## Repères (état validé)

| Élément | Valeur |
|---|---|
| Pi du Waffle | Ubuntu Server 22.04, ROS2 **Humble**, aarch64 |
| Hostname / user SSH | `waffleBot` / `waffle_user` |
| IP du Pi | **`10.191.69.104`** — ⚠️ **DHCP**, peut changer après reboot (voir §1) |
| `ROS_DOMAIN_ID` | **30** — identique dans **toutes** les fenêtres, sinon rien ne communique |
| Modèle / lidar | `TURTLEBOT3_MODEL=waffle_pi`, `LDS_MODEL=LDS-01` |
| Workspaces sur le Pi | `~/turtlebot3_ws` (stack turtlebot3), `~/mlf_ws` (notre package) |
| App côté UNO Q | `~/ArduinoApps/mlf_coindetector` — `WAFFLE_HOST` doit = IP du Pi |

Ces variables d'env sont déjà dans le `~/.bashrc` du Pi (chargées à chaque connexion SSH).

---

## 1. Trouver l'IP du Pi (depuis le PC Windows, PowerShell)

L'IP est en DHCP. Si `10.191.69.104` ne répond plus, retrouve-la :

**a) Par le hostname (mDNS)** — le plus simple :
```powershell
ping wafflebot.local
```

**b) Par scan ARP** (si le `.local` ne résout pas) — remplace `10.191.69` par ton sous-réseau
(`ipconfig` → ligne « IPv4 » de ta carte Wi-Fi) :
```powershell
$tasks = 1..254 | ForEach-Object { (New-Object System.Net.NetworkInformation.Ping).SendPingAsync("10.191.69.$_",250) }
[System.Threading.Tasks.Task]::WaitAll($tasks); Start-Sleep 1
arp -a | Select-String "b8-27-eb|dc-a6-32|e4-5f-01|d8-3a-dd|2c-cf-67"   # préfixes MAC Raspberry Pi
```
La MAC du Pi de ce Waffle est `e4-5f-01-d3-22-e1`.

**c) Vérifier que le SSH répond :**
```powershell
Test-NetConnection -ComputerName 10.191.69.104 -Port 22 -InformationLevel Detailed
```

> Si l'IP a changé, pense à mettre à jour `WAFFLE_HOST` dans `python/main.py` (§4).

---

## 2. Se connecter — **deux** sessions SSH

On utilise deux fenêtres : une pour le **bringup**, une pour le **nœud joystick**.
Ouvre deux terminaux (2× PowerShell, ou 2 onglets VS Code) et dans chacun :

```powershell
ssh -o ServerAliveInterval=60 waffle_user@10.191.69.104
```

> `-o ServerAliveInterval=60` = garde la connexion en vie.
> ⚠️ Si le **PC se met en veille**, le SSH coupe et les programmes s'arrêtent. La veille sur
> secteur a été désactivée (`powercfg /change standby-timeout-ac 0`). Pour la rétablir :
> `powercfg /change standby-timeout-ac 30`.

---

## 3. Démarrer le robot

### 🛑 Sécurité d'abord
- Robot **roues en l'air** (sur une cale) tant que tu n'as pas vérifié le sens.
- Batterie LiPo **chargée** (≥ ~11,5 V). Un **bip + arrêt** = alarme de sous-tension → recharger.
- OpenCR : interrupteur **POWER sur ON**, LED rouge allumée, câble micro-USB relié au Pi.

### Session A — bringup (drivers du robot)
```bash
ros2 launch turtlebot3_bringup robot.launch.py
```
Attends de voir :
```
[turtlebot3_ros-3] ... turtlebot3_node ... Run!
[turtlebot3_ros-3] ... diff_drive_controller ... Run!
```
**Laisse cette fenêtre tourner.**

### Session B — nœud joystick (reçoit l'UDP du palet)
```bash
ros2 run mlf_coin_teleop joystick_teleop
```
Doit afficher :
```
[INFO] ... Écoute joystick UDP sur :5005 -> publie /cmd_vel (max_lin=0.15 m/s, max_ang=0.8 rad/s)
```
**Laisse aussi cette fenêtre tourner.**

> Vérif rapide que les deux sessions se voient : dans chacune, `echo $ROS_DOMAIN_ID` doit
> renvoyer **30**, et `ros2 node list` doit lister `/turtlebot3_node`, `/diff_drive_controller`.

---

## 4. Côté UNO Q

Dans ton SSH sur l'Arduino :
```bash
cd ~/ArduinoApps/mlf_coindetector
git pull
```
> Si `git pull` est **bloqué** (App Lab a modifié `sketch.yaml`/`app.yaml`) :
> ```bash
> git checkout -- sketch/sketch.yaml sketch/app.yaml && git pull
> ```

Vérifie que l'IP du Pi est bonne :
```bash
grep WAFFLE_HOST python/main.py     # -> doit = l'IP du Pi
```
Puis dans **App Lab → Run**. L'app envoie `{jx, jy}` en UDP à chaque détection de palet.

> Réseau : l'UNO Q doit être sur le **même LAN** que le Pi. Test : `ping 10.191.69.104` depuis l'UNO Q.

---

## 5. Utilisation — comportement palet = joystick

Position du palet dans la mire (par rapport au **centre**) :

| Palet | Robot |
|---|---|
| au centre | à l'arrêt (zone morte / neutre) |
| vers le haut | avance (`linear.x > 0`) |
| vers le bas | recule |
| vers la droite | tourne à droite |
| haut-droite | diagonale avant-droite (courbe) |
| **retiré / perdu** | **arrêt automatique** après 0,5 s (watchdog) |

> Orientation : « l'avant du robot » = le côté qui avance quand le palet est en haut.
> Si un axe semble inversé, voir `JOY_INVERT_X` / `JOY_INVERT_Y` (§9) — et vérifie d'abord que
> le robot est posé dans le bon sens.

---

## 6. Tester SANS l'UNO Q (envoyer l'UDP depuis le PC)

Pratique pour valider le robot seul. Roues en l'air. Depuis PowerShell sur le PC :

```powershell
$c = New-Object System.Net.Sockets.UdpClient
$ip = "10.191.69.104"; $port = 5005
$msg = '{"jx":0.0,"jy":0.6}'          # avance ; jx=droite, jy=avant, chacun dans [-1,1]
$b = [System.Text.Encoding]::UTF8.GetBytes($msg)
for ($i=0; $i -lt 20; $i++) { [void]$c.Send($b, $b.Length, $ip, $port); Start-Sleep -Milliseconds 100 }
$c.Close()
```
Change `$msg` : `{"jx":0.6,"jy":0.0}` (virage droite), `{"jx":0.5,"jy":0.5}` (diagonale avant-droite).
Le robot s'arrête ~0,5 s après la fin de l'envoi (watchdog).

> Pour visualiser la commande : dans une 3ᵉ session SSH sur le Pi, `ros2 topic echo /cmd_vel`.

---

## 7. Arrêt propre

- **Nœud joystick / bringup** : `Ctrl+C` dans chaque fenêtre.
- Si les roues restent en mouvement après un test `ros2 topic pub` manuel, envoie un stop :
  ```bash
  ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}"
  ```
- Éteins l'OpenCR (interrupteur) et débranche/retire la batterie si tu ranges le robot.

---

## 8. Dépannage (pièges rencontrés)

| Symptôme | Cause / correctif |
|---|---|
| Roues ne bougent pas, `Unknown topic '/cmd_vel'`, `ros2 node list` ne voit pas le robot | **`ROS_DOMAIN_ID` différent** entre les fenêtres. Vérifie `echo $ROS_DOMAIN_ID`=30 partout ; reconnecte des SSH propres. |
| Bringup échoue : `package 'ld08_driver' not found` | Mauvais lidar. `export LDS_MODEL=LDS-01` (déjà dans `~/.bashrc`). |
| Roues ne s'arrêtent pas après un `ros2 topic pub` | Le contrôleur garde la dernière consigne. Envoyer un Twist nul (§7). Notre nœud, lui, a un watchdog → non concerné. |
| Robot **bipe puis s'arrête** | **Batterie basse** (< ~11 V) : protection sous-tension de l'OpenCR. Recharger le LiPo ou brancher l'alim 12 V (SMPS). |
| `git pull` bloqué côté UNO Q | App Lab a modifié `sketch.yaml`/`app.yaml` : `git checkout -- sketch/sketch.yaml sketch/app.yaml && git pull`. |
| `apt` interrompu par des écrans `needrestart` en boucle | `sudo sed -i "s/#$nrconf{restart} = 'i';/$nrconf{restart} = 'a';/" /etc/needrestart/needrestart.conf` (mode auto). |
| SSH coupé (PC en veille) → build/bringup tués | Veille secteur désactivée (`powercfg`). Pour les tâches longues, lancer dans `tmux` (`tmux new -s x`, `Ctrl+B d` pour détacher, `tmux attach -t x`). |
| Directions inversées | Robot posé à l'envers, ou `JOY_INVERT_X/Y` (§9). |

---

## 9. Paramètres clés

**Côté UNO Q — `python/main.py` :**
```python
ROS_ENABLED  = True
WAFFLE_HOST  = "10.191.69.104"   # IP du Pi (à mettre à jour si le DHCP change)
WAFFLE_PORT  = 5005
JOY_INVERT_X = False             # True: inverse gauche/droite
JOY_INVERT_Y = False             # True: inverse avant/arrière
```

**Côté nœud — paramètres ROS2** (défauts, surchargeables au lancement) :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `udp_port` | 5005 | port UDP d'écoute |
| `max_linear` | 0.15 | vitesse d'avance max (m/s) |
| `max_angular` | 0.8 | vitesse de rotation max (rad/s) |
| `deadzone` | 0.12 | zone morte (palet ~centré = stop) |
| `timeout` | 0.5 | s sans paquet avant arrêt de sécurité |

Ex. pour aller plus doucement :
```bash
ros2 run mlf_coin_teleop joystick_teleop --ros-args -p max_linear:=0.10 -p max_angular:=0.5
```

---

## Annexe A — Installation du Pi depuis zéro (si reflash de la SD)

1. **Flasher** Ubuntu Server 22.04.5 LTS 64-bit avec Raspberry Pi Imager. Pré-régler (⚙️) :
   hostname, user/mot de passe, Wi-Fi (+ pays), **SSH activé**.

2. **ROS2 Humble** (ros-base) :
   ```bash
   sudo apt update && sudo apt install -y locales software-properties-common curl
   sudo locale-gen en_US en_US.UTF-8 && sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
   sudo add-apt-repository universe -y
   export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
   curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo $VERSION_CODENAME)_all.deb"
   sudo apt install -y /tmp/ros2-apt-source.deb
   sudo apt update && sudo apt upgrade -y && sudo apt install -y ros-humble-ros-base ros-dev-tools
   ```

3. **Variables d'env** (dans `~/.bashrc`) :
   ```bash
   echo 'source /opt/ros/humble/setup.bash'            >> ~/.bashrc
   echo 'export ROS_DOMAIN_ID=30 #TURTLEBOT3'          >> ~/.bashrc
   echo 'export TURTLEBOT3_MODEL=waffle_pi'            >> ~/.bashrc
   echo 'export LDS_MODEL=LDS-01'                      >> ~/.bashrc
   ```

4. **Stack turtlebot3** :
   ```bash
   mkdir -p ~/turtlebot3_ws/src && cd ~/turtlebot3_ws/src
   git clone -b humble https://github.com/ROBOTIS-GIT/turtlebot3.git
   cd ~/turtlebot3_ws && sudo rosdep init; rosdep update
   rosdep install --from-paths src --ignore-src -r -y
   colcon build --symlink-install --parallel-workers 1
   echo 'source ~/turtlebot3_ws/install/setup.bash' >> ~/.bashrc && source ~/.bashrc
   sudo cp $(ros2 pkg prefix turtlebot3_bringup)/share/turtlebot3_bringup/script/99-turtlebot3-cdc.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

5. **Firmware OpenCR** (OpenCR alimenté + relié en USB) :
   ```bash
   sudo dpkg --add-architecture armhf && sudo apt-get update && sudo apt-get install -y libc6:armhf
   export OPENCR_PORT=/dev/ttyACM0 OPENCR_MODEL=waffle
   rm -rf ./opencr_update.tar.bz2
   wget https://github.com/ROBOTIS-GIT/OpenCR-Binaries/raw/master/turtlebot3/ROS2/latest/opencr_update.tar.bz2
   tar -xvf opencr_update.tar.bz2 && cd ./opencr_update
   ./update.sh $OPENCR_PORT $OPENCR_MODEL.opencr
   ```

6. **Notre package** :
   ```bash
   cd ~ && git clone https://github.com/medkar/MLF_coinDetector.git
   mkdir -p ~/mlf_ws/src && cp -r ~/MLF_coinDetector/ros2/mlf_coin_teleop ~/mlf_ws/src/
   cd ~/mlf_ws && colcon build --symlink-install
   echo 'source ~/mlf_ws/install/setup.bash' >> ~/.bashrc && source ~/.bashrc
   ```

Ensuite, démarrage normal = §3.
