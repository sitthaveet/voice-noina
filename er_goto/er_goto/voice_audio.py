# SPDX-License-Identifier: GPL-3.0-only
"""Full-duplex USB audio through the XVF3800 (16 kHz USB firmware)."""

from contextlib import suppress
import os
import queue
import threading

import numpy as np


class RobotAudio:
    def __enter__(self):
        import sounddevice as sd

        selector = os.environ.get('RESPEAKER_DEVICE', '').casefold()
        devices = sd.query_devices()
        matches = []
        for i, device in enumerate(devices):
            name = device['name'].casefold()
            match = (selector == str(i) or selector in name) if selector else (
                'respeaker' in name or 'xvf3800' in name)
            if match and device['max_input_channels'] and device['max_output_channels']:
                matches.append(i)
        if len(matches) != 1:
            raise RuntimeError('Select one duplex reSpeaker with RESPEAKER_DEVICE '
                               '(see python3 -m sounddevice)')
        index = matches[0]
        self.input = queue.Queue(maxsize=25)
        self.output = bytearray()
        self.lock = threading.Lock()
        self.error = None
        # Read all capture channels so ALSA cannot mix raw mics into processed channel 0.
        self.stream = sd.Stream(device=index, samplerate=16000, blocksize=320, dtype='int16',
                                channels=(devices[index]['max_input_channels'],
                                          min(2, devices[index]['max_output_channels'])),
                                callback=self._callback)
        return self

    def start(self):
        self.stream.start()

    def _callback(self, indata, outdata, frames, timing, status):
        outdata.fill(0)
        try:
            self.input.put_nowait(indata[:, 0].astype('<i2', copy=False).tobytes())
        except queue.Full:
            self.error = 'microphone stream fell behind'
        with self.lock:
            size = min(len(self.output), frames * 2)
            if size:
                samples = np.frombuffer(self.output[:size], dtype='<i2')
                outdata[:len(samples)] = samples[:, None]
                del self.output[:size]

    def read(self):
        if self.error or not self.stream.active:
            raise RuntimeError(self.error or 'reSpeaker stream stopped')
        try:
            chunks = [self.input.get(timeout=0.5)]
        except queue.Empty as exc:
            raise RuntimeError('reSpeaker microphone stopped delivering audio') from exc
        # Take the whole backlog: one block per call cannot catch up once the CPU is busy.
        with suppress(queue.Empty):
            while True:
                chunks.append(self.input.get_nowait())
        return b''.join(chunks)

    def play(self, pcm):
        with self.lock:
            if len(pcm) % 2 or len(self.output) + len(pcm) > 32000:
                raise RuntimeError('Invalid or delayed GPT-Live playback')
            self.output.extend(pcm)

    def __exit__(self, *exc):
        self.stream.close()
