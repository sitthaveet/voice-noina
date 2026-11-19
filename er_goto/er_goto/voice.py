# SPDX-License-Identifier: GPL-3.0-only
"""GPT-Live session and the thread-safe handoff to the navigation loop."""

import asyncio
import base64
from contextlib import suppress
import json
import os
import queue
import threading

from er_goto.commands import Command
from er_goto.voice_audio import RobotAudio

SESSION = {
    'model': 'gpt-live-1',
    'audio': {'format': {'type': 'audio/pcm', 'rate': 16000}},
    'instructions': (
        "You are Noina (น้อยหน่า, pronounced Noi-Na), a conversational robot. "
        "Reply briefly and follow the speaker's choice of language. "
        "Listen while speaking and yield to interruptions. Delegate explicit requests to walk "
        "to a target or stop walking, including a standalone request to stop. Ask what to walk to "
        "if the destination is ambiguous. Chat normally otherwise. Announce navigation results "
        "only when supplied by the robot. Acknowledging a request does not mean you arrived."
    ),
    'delegation': {'type': 'responses', 'responses': {
        'model': 'gpt-6-luna',
        'parallel_tool_calls': False,
        'instructions': (
            "Call walk_to once per explicit movement request. For go, supply only the visual "
            "target description, preserving identifying details. For stop use an empty target. "
            "Questions, examples, negations and requests to stop speaking are not movement "
            "commands. Ask for clarification when needed. Never retry superseded commands."
        ),
        'tools': [{
            'type': 'function', 'name': 'walk_to',
            'description': 'Request a walk to a visually described target, or stop walking.',
            'strict': True,
            'parameters': {'type': 'object', 'additionalProperties': False,
                           'properties': {'action': {'type': 'string', 'enum': ['go', 'stop']},
                                          'target': {'type': 'string'}},
                           'required': ['action', 'target']},
        }],
    }},
}


class VoiceCommands:
    """Keep the newest request. Disconnect permanently invalidates this mailbox."""

    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.stop = None
        self.error = None
        self.latest = -1
        self.results = queue.SimpleQueue()

    def submit(self, offset, arguments):
        try:
            args = json.loads(arguments)
        except (TypeError, ValueError):
            return 'invalid arguments'
        if not isinstance(args, dict) or set(args) != {'action', 'target'}:
            return 'invalid arguments'
        action, target = args['action'], args['target']
        if action not in ('stop', 'go') or not isinstance(target, str):
            return 'invalid command'
        target = target.strip()
        if (action == 'go') != bool(target) or len(target) > 300:
            return 'invalid target'
        with self.condition:
            if self.error or offset < self.latest or (offset == self.latest and action != 'stop'):
                return 'superseded'
            self.latest = offset
            command = Command(action, target, offset)
            if action == 'stop':
                self.stop = command
                self.pending = None
            else:
                self.pending = command
            self.condition.notify()
        return 'accepted'

    def disconnect(self, reason):
        with self.condition:
            self.error = reason
            self.pending = None
            self.condition.notify_all()

    def get(self, timeout=0):
        with self.condition:
            self.condition.wait_for(lambda: self.stop or self.pending or self.error, timeout)
            if self.error:
                raise ConnectionError(self.error)
            if self.stop:
                command, self.stop = self.stop, None
                return command
            command, self.pending = self.pending, None
            return command

    def report(self, command, status):
        self.results.put((command, status))


class LiveEvents:
    def __init__(self, commands):
        self.commands = commands
        self.offsets = {}
        self.handled = set()

    def handle(self, event):
        """Translate completed function calls only. Never execute transcript fragments."""
        kind = event['type']
        if kind == 'session.delegation.created':
            self.offsets[event['delegation']['id']] = event['offset_ms']
        elif kind == 'response.event':
            nested = event['event']
            if nested['type'] in ('response.failed', 'response.incomplete', 'error'):
                raise RuntimeError('Live navigation delegation failed')
            if nested['type'] != 'response.output_item.done':
                return []
            item = nested['item']
            if item['type'] != 'function_call' or item['call_id'] in self.handled:
                return []
            self.handled.add(item['call_id'])
            offset = self.offsets.get(event.get('delegation_id'))
            result = 'unknown tool or delegation'
            if item.get('status', 'completed') != 'completed':
                result = 'incomplete call'
            elif offset is not None and item['name'] == 'walk_to':
                result = self.commands.submit(offset, item['arguments'])
            return [{'type': 'response.item.create',
                     'item': {'type': 'function_call_output', 'call_id': item['call_id'],
                              'output': json.dumps({'status': result})}},
                    {'type': 'response.create'}]
        elif kind == 'error':
            raise RuntimeError(event['error'].get('message', 'Live API error'))
        elif kind == 'session.closed':
            raise ConnectionError('Live session closed')
        return []


async def _conversation(socket, audio, commands, log):
    async def send(event):
        await asyncio.wait_for(socket.send(json.dumps(event)), 2)

    events = LiveEvents(commands)
    await send({'type': 'session.start', 'session': SESSION})
    while True:
        event = json.loads(await asyncio.wait_for(socket.recv(), 10))
        if event['type'] == 'session.started':
            break
        if event['type'] in ('error', 'session.closed'):
            events.handle(event)

    async def receive():
        async for message in socket:
            event = json.loads(message)
            if event['type'] == 'session.output_audio.delta':
                audio.play(base64.b64decode(event['delta'], validate=True))
            else:
                replies = events.handle(event)
                if replies:
                    call = event['event']['item']
                    log.info(f"Voice tool {call['call_id']}: {call['arguments']} "
                             f"-> {replies[0]['item']['output']}")
                for reply in replies:
                    await send(reply)
        raise ConnectionError('Live connection ended')

    async def transmit():
        while True:
            chunk = await asyncio.to_thread(audio.read)
            await send({'type': 'session.input_audio.append',
                        'audio': base64.b64encode(chunk).decode('ascii')})
            while not commands.results.empty():
                command, status = commands.results.get_nowait()
                if command.offset == commands.latest:
                    await send({'type': 'session.commentary.append', 'delegation_id': None,
                                'content': json.dumps({'target': command.target, 'navigation': status},
                                                      ensure_ascii=False)})

    audio.start()
    log.info('GPT-Live is listening. Ctrl+C stops and exits.')
    tasks = [asyncio.create_task(receive()), asyncio.create_task(transmit())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _session(commands, log):
    from websockets.asyncio.client import connect

    with RobotAudio() as audio:
        async with connect('wss://api.openai.com/v1/live/sessions',
                           additional_headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']},
                           open_timeout=10, close_timeout=1, ping_interval=2, ping_timeout=2) as socket:
            try:
                await _conversation(socket, audio, commands, log)
            finally:
                commands.disconnect('Voice session ended')
                with suppress(Exception):
                    await asyncio.wait_for(socket.send('{"type":"session.close"}'), 0.5)


class VoiceSession:
    def __init__(self, log):
        self.commands = VoiceCommands()
        self.log = log
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.task = self.loop.create_task(_session(self.commands, self.log))
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log.error(f'Voice session failed: {exc}')
        finally:
            self.commands.disconnect('Voice session ended')
            self.loop.close()

    def start(self):
        self.thread.start()

    def close(self):
        self.commands.disconnect('Voice mode closed')
        if self.thread.is_alive():
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(lambda: self.task.cancel())
            self.thread.join(timeout=2)
