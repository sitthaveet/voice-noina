# Noina (น้อยหน่า)

Noina gives the RoboParty RPO humanoid real-time voice conversation and spoken walking, stopping, and hand-wave greetings. This fork of [RoboParty's deployment framework](https://github.com/Roboparty/roboparty_deploy) adds `er_goto` for the RDK X5. GPT-Live (`gpt-live-1.1`) handles conversation and delegates explicit movement requests to `walk_to` and `wave`. GPT-6.2 Astra locates the requested object in a RealSense image, and the local ROS 2 node uses aligned depth to publish velocity commands. Voice mode listens while the session is running.

[Run guide](https://noina-run-guide.royallake19.chatgpt.site) · [中文](README_CN.md) · [License](LICENSE)

## Set up the RDK X5

Use ROS 2 Humble, `colcon`, and librealsense2 2.58 or newer. The launchers also detect Jazzy. Humble is the build-validated path documented in [Common Operation](docs/operation.md). Before live operation, complete the [RPO deployment and calibration guide](docs/rpo_deploy.md) and confirm that the robot walks under gamepad control.

Plug the RealSense D435i into USB 3 and connect the reSpeaker XVF3800 to the X5 over USB. Use the reSpeaker's 16 kHz USB firmware. Plug the powered speaker into the reSpeaker's 3.5 mm output so the array's echo canceller receives the playback signal. [reSpeaker firmware and wiring](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/)

On a new X5, clone the fork with `git clone https://github.com/sitthaveet/voice-noina.git`. While the changes are unpushed, also copy the updated `er_goto/` and `tools/` files (including the new vision client and inference patch) and `.env.example` from your development checkout into the matching X5 checkout. Keep the X5’s own `.env`. Run the commands below from `voice-noina/` on the X5. Confirm the first command lists both files before continuing.

```bash
ls er_goto/er_goto/vision_client.py tools/patches/inference-voice-wave.patch
git submodule update --init --recursive
sudo apt update
sudo apt install -y screen python3-pip python3-opencv python3-numpy python3-pytest libportaudio2
python3 -m pip install --user 'openai>=2' sounddevice 'websockets>=14,<17'
python3 -m sounddevice
```

Recursive submodule initialization fetches the RealSense ROS wrapper. Use the system OpenCV package to keep NumPy compatible with ROS Humble. The audio device list should show a reSpeaker or XVF3800 with both input and output channels. If several devices match, set `RESPEAKER_DEVICE` to the intended numeric index in `.env`.

```bash
cp -n .env.example .env
chmod 600 .env
nano .env
```

Set `OPENAI_API_KEY` for GPT-Live (`gpt-live-1.1`), its movement-command delegate (`gpt-6-luna`), and camera targeting (`gpt-6.2-astra`). The X5 needs internet access even in a dry run. Keep the key in the git-ignored `.env` at the root of the checkout.

## Run a voice dry run

Keep the robot powered off and run the launcher from the repository root. The launcher builds the node, starts or reuses the RealSense `er_camera_session` in `screen`, and checks the RGB and aligned depth streams.

```bash
./tools/start_er_goto.sh --voice --dry-run
```

Wait for **GPT-Live is listening**. In another terminal from the repository root, monitor the dry-run topic. These commands assume Humble.

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ros2 topic echo /cmd_vel_test --qos-reliability best_effort
```

1. Ask an ordinary question, then interrupt Noina's reply. Confirm you hear a response and the velocity topic stays quiet.
2. Place a red chair in view and say “Walk to the red chair.” Check that movement commands appear. Give a different destination and look for a zero command before the new pursuit.
3. During pursuit, say “Stop walking.” Check for zero velocity followed by silence on the topic.
4. Say “Wave.” Look for `Dry run: would play policy_wave.onnx` in the voice terminal. Try “Walk to the chair and say hi to it.” Move the camera to simulate arrival and check that the wave is logged only after arrival.

Microphone audio goes to OpenAI while voice mode runs. Astra receives the requested target and camera frames used to locate it.

## Run on the robot

Stop the dry run and its topic monitor with **Ctrl+C**. Confirm manual walking and follow the [staged test plan](er_goto/README.md#test-plan). Do the first live check with the RPO supported on the crane. Move to a clear floor only after the crane check passes. The obstacle check is off by default and the BeyondMimic tilt cutoff ordinarily stays inactive.

1. **Terminal 1, on the X5.** From the repository root, start the RPO with BeyondMimic. This is also the default when you omit the arguments.

   ```bash
   ./tools/start_robot.sh --robot rpo --policy beyondmimic
   ```

   The launcher applies the voice-wave patch before building, then starts `inference_session` and `joy_session` in the background. From a fresh start with motors disabled, press **X** to enable motors, then **A** to move to the starting pose. Wait for `Motors reset` in the inference log (`screen -r inference_session`). Press **B** once to start inference. Keep the gamepad in control and let the robot stabilize in walking mode.

2. **Terminal 2, on the X5.** From the same repository root, start voice control.

   ```bash
   ./tools/start_er_goto.sh --voice
   ```

   The launcher starts or reuses the RGB-D camera. Wait for **GPT-Live is listening** and confirm the robot is stable. Press **Y** once to hand control to `/cmd_vel`. The inference log should say `Controlled by /cmd_vel`.

3. **Try a command.** “Wave” or “Dance” plays one hand wave with `policy_wave.onnx` and `wave.npz`, then returns to walking mode. “Walk to the red chair and say hi to it” queues the wave until arrival (about 0.5 m away). “Stop walking” cancels the walk, a queued greeting, or an active voice wave. A replacement destination discards the old greeting.

**Keep the gamepad in hand.** During voice control, **Y** takes control back and **B** pauses inference. A spoken stop depends on microphone, network, and model latency. If the voice node dies mid-walk, inference can retain the last velocity command.

## Shut down

1. In live mode, press **Y** if voice has control. Confirm `Controlled by joy` in the inference log, then press **Ctrl+C** in the voice terminal. In a dry run, **Ctrl+C** alone ends the session.
2. For a live shutdown, support the robot first. With inference running, press **B** to pause, then **X** to disable the enabled motors. After confirming the motors are disabled, close the background sessions. The camera can stay up between voice runs.

   ```bash
   screen -S inference_session -X quit
   screen -S joy_session -X quit
   ```

3. When finished with the camera (live or dry run), close its session.

   ```bash
   screen -S er_camera_session -X quit
   ```

## Troubleshooting

| Symptom or task | What to do |
| --- | --- |
| No audio or several reSpeaker devices | Run `python3 -m sounddevice`, set `RESPEAKER_DEVICE` in `.env` to the duplex device index, and restart voice mode. |
| Camera busy or no frames | Check `log/er_camera_session.log` and the USB 3 connection. Stop any parkour camera sessions from `tools/start_camera.sh`, then retry the voice launcher with `--restart-camera`. |
| Speech works but the target is missed | With the robot off, run `./tools/start_er_goto.sh --dry-run --debug-images`. At `er_goto>`, type `walk to the red chair`, then `stop` or `quit`. Inspect Astra’s annotated frames under `~/er_goto_debug/`. For typed live control, use `./tools/start_er_goto.sh` after the same gamepad startup and handoff. |
| Wave unavailable or refused | From a ROS shell with the workspace sourced, run `ros2 service type /inference_node/wave`. Expect `std_srvs/srv/SetBool`. If missing, check the patch is present and start the RPO with BeyondMimic. If refused, confirm inference is running in walking mode and the log says `Controlled by /cmd_vel`. |
| Need the robot log | Run `screen -r inference_session`. Press **Ctrl+A**, then **D** to detach. |

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
