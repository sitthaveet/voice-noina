# Overview
This is the roboparty deploy repository that integrated with Google Gemini ER API and camera. 

Our goal is to 
1. Run the script to make the robot to be able to listen and speak in real-time conversation. 
2. It can interact and talk with humans.
3. The only tool call it can use from voice is to order the robot to walk to something, insert as a prompt (walk_to) into Gemini ER. Voice can stop the robot from walking.

Further implementation (not yet)
- Use wake word to trigger the robot to listen and respond.

## Stack
Computer -  RDK X5 
Camera -  Realsense D435i
Microphone - reSpeaker XVF3800 With Case – USB 4-Mic Array
Speaker - generic 3.5mm jax speaker which will connect to reSpeaker directly

Real-time conversation - GPT-Live (gpt-live-1) https://developers.openai.com/api/docs/guides/live

## ENV
contains 
GEMINI_API_KEY
OPENAI_API_KEY

## Robot name
Noina (Noi-Na) or น้อยหน่า in Thai.