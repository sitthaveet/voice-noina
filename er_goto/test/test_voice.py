# SPDX-License-Identifier: GPL-3.0-only
"""Exercise voice navigation with simulated audio, Live events and a stationary robot."""

import asyncio
import base64
import json
import logging
import queue
import threading
from unittest.mock import Mock

import numpy as np
import pytest

from er_goto.commands import Command, NewCommand, command_loop
from er_goto.voice import LiveEvents, VoiceCommands, _conversation
from er_goto.voice_audio import RobotAudio


def args(action, target=''):
    return json.dumps({'action': action, 'target': target})


def delegation(offset):
    return {'type': 'session.delegation.created', 'delegation': {'id': str(offset)},
            'offset_ms': offset}


def call(offset, call_id, arguments, name='walk_to'):
    return {'type': 'response.event', 'delegation_id': str(offset),
            'event': {'type': 'response.output_item.done',
                      'item': {'type': 'function_call', 'name': name,
                               'call_id': call_id, 'arguments': arguments}}}


def test_only_completed_valid_calls_can_move():
    commands = VoiceCommands()
    events = LiveEvents(commands)
    events.handle(delegation(10))
    for event in [
        {'type': 'session.input_transcript.delta', 'delta': 'เดินไปที่เก้าอี้'},
        {'type': 'response.event', 'event': {'type': 'response.function_call_arguments.delta'}},
        call(10, 'a', args('go')), call(10, 'b', 'broken'), call(10, 'c', args('quit')),
        call(10, 'd', args('go', 'chair'), name='other'), call(8, 'e', args('go', 'chair')),
    ]:
        events.handle(event)
        assert commands.get() is None
    event = call(10, 'f', args('go', 'เก้าอี้สีแดง'))
    result, continuation = events.handle(event)
    assert json.loads(result['item']['output']) == {'status': 'accepted'}
    assert result['item']['call_id'] == 'f'
    assert continuation == {'type': 'response.create'}
    assert commands.get() == Command('go', 'เก้าอี้สีแดง', 10)
    assert events.handle(event) == []
    assert commands.get() is None


def test_stop_invalidates_old_calls_and_precedes_a_fresh_target():
    commands = VoiceCommands()
    commands.submit(1, args('go', 'chair'))
    commands.submit(3, args('stop'))
    assert commands.submit(2, args('go', 'table')) == 'superseded'
    assert commands.submit(3, args('go', 'table')) == 'superseded'
    commands.submit(4, args('go', 'desk'))
    assert commands.get().action == 'stop'
    assert commands.get().target == 'desk'
    assert commands.get() is None


@pytest.mark.parametrize('disconnect', [True, False])
def test_active_walk_zeroed_before_next_target_or_disconnect(disconnect):
    commands = VoiceCommands()
    commands.submit(1, args('go', 'chair'))
    node = Mock()
    actions = []

    def walk(target, poll):
        actions.append(target)
        if target == 'chair':
            commands.submit(2, args('go', 'desk'))
            if disconnect:
                commands.disconnect('lost microphone')
            raise NewCommand(poll())
        return 0

    node.run.side_effect = walk
    node.publish_zero.side_effect = lambda **kwargs: actions.append('zero')

    def report(command, status):
        actions.append(status)
        commands.disconnect('done')

    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    expected = ['chair', 'zero', 'failed'] if disconnect else [
        'chair', 'zero', 'desk', 'zero', 'arrived']
    assert actions == expected
    assert commands.submit(4, args('go', 'desk')) == 'superseded'


def test_duplex_callback_selects_processed_mic_and_plays_on_both_channels():
    audio = RobotAudio()
    audio.lock = threading.Lock()
    audio.input = queue.Queue()
    audio.output = bytearray()
    outgoing = np.ones((4, 2), dtype=np.int16)
    microphones = np.arange(24, dtype=np.int16).reshape(4, 6)
    audio.play(np.array([100, -200], dtype='<i2').tobytes())
    audio._callback(microphones, outgoing, 4, None, None)
    assert np.frombuffer(audio.input.get(), dtype='<i2').tolist() == [0, 6, 12, 18]
    assert outgoing.tolist() == [[100, 100], [-200, -200], [0, 0], [0, 0]]


def test_live_session_streams_audio_and_delivers_actual_navigation_outcome():
    async def exercise():
        sent = []
        inbox = asyncio.Queue()
        commands = VoiceCommands()
        pcm = b'\x01\x00' * 320
        audio = Mock()
        audio.read.return_value = pcm

        class Socket:
            async def send(self, raw):
                event = json.loads(raw)
                sent.append(event)
                if event['type'] == 'session.start':
                    inbox.put_nowait({'type': 'session.started'})
                elif event['type'] == 'session.input_audio.append' and len(sent) == 2:
                    inbox.put_nowait({'type': 'session.output_audio.delta', 'delta': event['audio']})
                    inbox.put_nowait(delegation(5))
                    inbox.put_nowait(call(5, 'abc', args('go', 'chair')))
                elif event['type'] == 'response.create':
                    commands.report(commands.get(), 'arrived')
                elif event['type'] == 'session.commentary.append':
                    inbox.put_nowait({'type': 'session.closed'})

            async def recv(self):
                return json.dumps(await inbox.get())

            def __aiter__(self):
                return self

            __anext__ = recv

        with pytest.raises(ConnectionError, match='closed'):
            await asyncio.wait_for(_conversation(Socket(), audio, commands, logging.getLogger()), 2)
        audio.play.assert_called_once_with(pcm)
        assert base64.b64decode(sent[1]['audio']) == pcm
        comment = next(e for e in sent if e['type'] == 'session.commentary.append')
        assert json.loads(comment['content']) == {'target': 'chair', 'navigation': 'arrived'}

    asyncio.run(exercise())


def test_stop_interrupts_navigation_before_reporting_stopped():
    commands = VoiceCommands()
    commands.submit(1, args('go', 'chair'))
    node = Mock()
    actions = []

    def waiting_for_gemini(target, poll):
        commands.submit(2, args('stop'))
        raise NewCommand(poll())

    def report(command, status):
        actions.append(status)
        commands.disconnect('test complete')

    node.run.side_effect = waiting_for_gemini
    node.publish_zero.side_effect = lambda **kwargs: actions.append('zero')
    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    node.run.assert_called_once()
    assert actions == ['zero', 'zero', 'stopped']


def test_missing_microphone_frames_end_session():
    async def exercise():
        class OpenSocket:
            async def send(self, raw):
                pass

            async def recv(self):
                return '{"type":"session.started"}'

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

        audio = Mock()
        audio.read.side_effect = RuntimeError('microphone disconnected')
        with pytest.raises(RuntimeError, match='microphone disconnected'):
            await asyncio.wait_for(_conversation(OpenSocket(), audio, VoiceCommands(),
                                                 logging.getLogger()), 1)
    asyncio.run(exercise())
