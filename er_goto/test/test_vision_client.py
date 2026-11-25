# SPDX-License-Identifier: GPL-3.0-only
"""Verify the camera request and error handling without calling the API."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from er_goto.vision_client import Point, VisionError, VisionFatalError, VisionPointer


def test_camera_request_uses_astra_fast_medium():
    with patch('openai.OpenAI') as sdk:
        create = sdk.return_value.responses.create
        create.return_value.status = 'completed'
        create.return_value.output_text = '[{"point": [200, 750], "label": "chair"}]'
        points, _ = VisionPointer(api_key='test').point(b'JPEG', 'blue chair')

    sdk.assert_called_once_with(api_key='test', timeout=15.0, max_retries=1)
    params = create.call_args.kwargs
    assert params['model'] == 'gpt-6.2-astra'
    assert params['service_tier'] == 'fast'
    assert params['reasoning'] == {'effort': 'medium'}
    prompt, image = params['input'][0]['content']
    assert 'blue chair' in prompt['text']
    assert image == {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,SlBFRw=='}
    assert points == [Point(y=200, x=750, label='chair')]


@pytest.mark.parametrize(('status', 'expected'), [(401, VisionFatalError), (429, VisionError),
                                                  (503, VisionError)])
def test_camera_http_failure_reaches_navigation(status, expected, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_error(status)

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        monkeypatch.setenv('OPENAI_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
        server.timeout = 3
        handler = Thread(target=server.handle_request)
        handler.start()
        try:
            with pytest.raises(expected):
                VisionPointer(api_key='test', retries=0).point(b'JPEG', 'chair')
        finally:
            handler.join()


def test_incomplete_camera_response_cannot_produce_a_fix():
    with patch('openai.OpenAI') as sdk:
        response = sdk.return_value.responses.create.return_value
        response.status = 'incomplete'
        response.output_text = '[{"point": [200, 750]}]'
        with pytest.raises(VisionError, match='incomplete'):
            VisionPointer(api_key='test').point(b'JPEG', 'chair')
