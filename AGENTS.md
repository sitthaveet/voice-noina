# Overview
This RoboParty deployment uses the OpenAI Responses API to locate targets in camera images.

Our goal is to 
1. Run the script to make the robot to be able to listen and speak in real-time conversation. 
2. It can interact and talk with humans.
3. The tool calls it can use from voice are
3.1 Order the robot to walk to something, insert as a prompt (walk_to) into GPT-6.2 Astra. Voice can stop the robot from walking.
3.2 Ask it dance with policy_wave.onnx


## Stack
Robot - RP0
Computer -  RDK X5 
Camera -  Realsense D435i
Microphone - reSpeaker XVF3800 With Case – USB 4-Mic Array
Speaker - generic 3.5mm jax speaker which will connect to reSpeaker directly

Real-time conversation - GPT-Live (gpt-live-1.1)

## ENV
contains
OPENAI_API_KEY

## Robot name
Noina (Noi-Na) or น้อยหน่า in Thai.