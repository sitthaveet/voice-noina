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

    def waiting_for_vision(target, poll):
        commands.submit(2, args('stop'))
        raise NewCommand(poll())

    def report(command, status):
        actions.append(status)
        commands.disconnect('test complete')

    node.run.side_effect = waiting_for_vision
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


def test_wave_accepts_only_empty_arguments_and_preserves_pending_destination():
    commands = VoiceCommands()
    events = LiveEvents(commands)
    events.handle(delegation(1))
    assert commands.submit(1, args('go', 'red chair')) == 'accepted'
    events.handle(delegation(2))
    result, _ = events.handle(call(2, 'invalid', '{"model":"policy_dance0.onnx"}', 'wave'))
    assert json.loads(result['item']['output'])['status'] == 'invalid arguments'
    request = call(2, 'hello', '{}', 'wave')
    events.handle(request)
    assert events.handle(request) == []
    assert commands.get() == Command('go_and_wave', 'red chair', 2)
    commands.submit(3, args('stop'))
    assert commands.submit(2, '{}', 'wave') == 'superseded'


@pytest.mark.parametrize('compound', [False, True])
@pytest.mark.parametrize('arrival', [False, True])
def test_wave_is_conditional_on_arrival(compound, arrival):
    commands = VoiceCommands()
    node = Mock()
    trace = []
    node.publish_zero.side_effect = lambda **kwargs: trace.append('zero')
    node.start_wave.side_effect = lambda: trace.append('wave') or 'wave started'
    commands.submit(1, args('go_and_wave' if compound else 'go', 'chair'))

    def walk(target, poll):
        trace.append(target)
        if not compound:
            commands.submit(2, '{}', 'wave')
            assert poll() is None
        return 0 if arrival else 1

    def report(command, status):
        assert command.target == 'chair'
        assert command.offset == (1 if compound else 2)
        trace.append(status)
        commands.disconnect('done')

    node.run.side_effect = walk
    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    assert trace == ['chair', 'zero'] + (['wave', 'wave started'] if arrival else ['failed'])


@pytest.mark.parametrize('replacement', ['stop', 'go'])
def test_replacement_during_arrival_cleanup_discards_greeting(replacement):
    commands = VoiceCommands()
    commands.submit(1, args('go_and_wave', 'chair'))
    node = Mock()
    node.run.return_value = 0

    def replace(**kwargs):
        if commands.latest == 1:
            commands.submit(2, args(replacement, 'desk' if replacement == 'go' else ''))

    node.publish_zero.side_effect = replace
    outcomes = []

    def report(command, status):
        outcomes.append(status)
        commands.disconnect('done')

    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    node.start_wave.assert_not_called()
    assert outcomes == ['stopped' if replacement == 'stop' else 'arrived']


def test_standalone_wave_and_stop_do_not_start_a_walk():
    commands = VoiceCommands()
    commands.submit(1, '{}', 'wave')
    node = Mock()
    node.start_wave.return_value = 'wave started'
    outcomes = []

    def report(command, status):
        outcomes.append(status)
        if command.action == 'wave':
            commands.submit(2, args('stop'))
        else:
            commands.disconnect('done')

    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    assert outcomes == ['wave started', 'stopped']
    node.run.assert_not_called()
    node.cancel_wave.assert_called_once()


def test_camera_failure_discards_wave_received_while_stopping():
    commands = VoiceCommands()
    commands.submit(1, args('go', 'chair'))
    node = Mock()
    node.run.side_effect = RuntimeError('camera disconnected')
    node.publish_zero.side_effect = lambda **_: commands.submit(2, '{}', 'wave')
    outcomes = []

    def report(command, status):
        outcomes.append((command.target, command.offset, status))
        commands.disconnect('done')

    with pytest.raises(ConnectionError):
        command_loop(node, commands.get, report)
    assert outcomes == [('chair', 2, 'failed')]
    node.start_wave.assert_not_called()
