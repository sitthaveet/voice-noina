# Noina (น้อยหน่า)

Noina gives the RoboParty RPO humanoid real-time voice conversation and spoken walk and stop commands. This fork of [RoboParty's deployment framework](https://github.com/Roboparty/roboparty_deploy) adds `er_goto` for the RDK X5. GPT-Live handles conversation and passes explicit movement requests to `walk_to`. Gemini Robotics-ER locates the requested object in a RealSense image, and the local ROS 2 node uses aligned depth to publish velocity commands. Voice mode listens while the session is running.

[中文](README_CN.md) · [License](LICENSE)

## Set up the RDK X5

Use ROS 2 Humble, `colcon`, and librealsense2 2.58 or newer. The launchers also detect Jazzy. Humble is the build-validated path documented in [Common Operation](docs/operation.md). Before live operation, complete the [RPO deployment and calibration guide](docs/rpo_deploy.md) and confirm that the robot walks under gamepad control.

Plug the RealSense D435i into USB 3 and connect the reSpeaker XVF3800 to the X5 over USB. Use the reSpeaker's 16 kHz USB firmware. Plug the powered speaker into the reSpeaker's 3.5 mm output so the array's echo canceller receives the playback signal. [reSpeaker firmware and wiring](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/)

Run these commands on the X5. If you already have a checkout, start at `git submodule update` from the repository root.

```bash
git clone https://github.com/sitthaveet/voice-noina.git
cd voice-noina
git submodule update --init --recursive
sudo apt update
sudo apt install -y screen python3-pip python3-opencv python3-numpy python3-pytest libportaudio2
python3 -m pip install --user google-genai sounddevice 'websockets>=14,<17'
python3 -m sounddevice
```

Recursive submodule initialization fetches the RealSense ROS wrapper. Use the system OpenCV package to keep NumPy compatible with ROS Humble. The audio device list should show a reSpeaker or XVF3800 with both input and output channels. If several devices match, set `RESPEAKER_DEVICE` to the intended numeric index in `.env`.

```bash
cp -n .env.example .env
chmod 600 .env
nano .env
```

Fill in `GEMINI_API_KEY` for Gemini Robotics-ER and `OPENAI_API_KEY` for GPT-Live. Keep the keys in the git-ignored `.env` at the root of the checkout.

## Run a voice dry run

Keep the robot powered off and run the launcher from the repository root. The launcher builds the node, starts or reuses the RealSense `er_camera_session` in `screen`, and checks the RGB and aligned depth streams.

```bash
./tools/start_er_goto.sh --voice --dry-run
```

Wait for **GPT-Live is listening**. In another terminal from the repository root, monitor the dry-run topic. These commands assume Humble.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 topic echo /cmd_vel_test --qos-reliability best_effort
```

1. Ask an ordinary question, then interrupt Noina's reply. Confirm you hear a response and the velocity topic stays quiet.
2. Place a red chair in view and say “Walk to the red chair.” Check that movement commands appear. Give a different destination and look for a zero command before the new pursuit.
3. During pursuit, say “Stop walking.” Check for zero velocity followed by silence on the topic.

Microphone audio goes to OpenAI while voice mode runs. Gemini receives the requested target and camera frames used to locate it.

## Run on the robot

Follow the [staged test plan](er_goto/README.md#test-plan), including the crane test before moving to a clear floor area. Stop the dry-run process before launching live mode.

**Terminal 1** starts the RPO with the default policy.

```bash
./tools/start_robot.sh
```

Use the [normal gamepad startup procedure](docs/operation.md#gamepad-controls) and let the robot balance. From the repository root in **terminal 2**, start voice mode.

```bash
./tools/start_er_goto.sh --voice
```

When the node is listening and the robot is balanced, press **Y** to switch to `/cmd_vel` control. Try “Walk to the red chair” and “Stop walking.”

**Keep the gamepad in hand.** Press **Y** again to take control back or **B** to pause inference. Spoken stops depend on audio capture, network, and model response. If the node is killed abruptly, the robot can retain its last velocity command.

## Stop and troubleshoot

Return control to the gamepad if live control is active, then press **Ctrl+C** in the `er_goto` terminal. The camera session stays running for reuse. Close it when you finish.

```bash
screen -S er_camera_session -X quit
```

For a full hardware shutdown, support the robot and follow the [documented shutdown order](docs/operation.md#startup-and-logs) to stop inference, disable motors, and close the robot sessions.

| If you need to | Use |
| --- | --- |
| Test Gemini and the camera with typed commands | Run `./tools/start_er_goto.sh --dry-run` with the robot off. At `er_goto>`, try `walk to the red chair`, `stop`, or `quit`. |
| Use typed commands on the robot | After the staged checks, run `./tools/start_er_goto.sh` in terminal 2 and use the same gamepad handoff. |
| Inspect or restart the camera | Check `log/er_camera_session.log` or run `screen -r er_camera_session`. Add `--restart-camera` to restart it. Stop the parkour camera sessions from `tools/start_camera.sh` before using `er_goto`. |
| Check where Gemini pointed | Add `--debug-images` to save annotated frames under `~/er_goto_debug/`. |
| Diagnose missing inference | Start `./tools/start_robot.sh` and inspect `screen -r inference_session`. Inside `screen`, press **Ctrl+A** then **D** to detach. |

## Repository map

| Path | Responsibility |
| --- | --- |
| [`er_goto/`](er_goto/README.md) | Voice, visual navigation, velocity publishing, parameters, safety details, and tests |
| `src/inference/` | Policy inference and robot-specific hardware configuration |
| `src/motors/`, `src/imu/`, `src/camera/` | Hardware drivers and camera integration |
| `tools/`, `scripts/` | Startup scripts, SDK examples, and motor zeroing |

See the [configuration reference](docs/configuration.md) and [Python SDK guide](docs/python_sdk.md) for the underlying RoboParty stack.

## License

Unless otherwise stated, original content authored by RoboParty in this repository is licensed under the [GNU General Public License v3.0 only](LICENSE) (`GPL-3.0-only`). Git submodules, packaged binaries, and other third-party materials retain their own license terms. See [LICENSES.md](LICENSES.md) for the license inventory and redistribution notes.
