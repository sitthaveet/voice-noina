# SPDX-License-Identifier: GPL-3.0-only
"""OpenAI image pointing and normalized point parsing."""

import base64
import json
import os
import re
from dataclasses import dataclass

DEFAULT_MODEL = 'gpt-6.2-astra'
DEFAULT_REASONING_EFFORT = 'medium'
DEFAULT_SERVICE_TIER = 'fast'

PROMPT_TEMPLATE = (
    'Point inside the visible part of the {target}. '
    'Answer as JSON: [{{"point": [y, x], "label": "<name>"}}]. '
    'Normalize y from top to bottom and x from left to right, each from 0 to 1000. '
    'If several match, put the best match first. If none is visible, answer [].'
)

_FENCE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)


@dataclass(frozen=True)
class Point:
    """One image point: y and x normalized to 0-1000."""
    y: float
    x: float
    label: str


class VisionError(Exception):
    """Transient failure (timeout, network, server or rate limit) after all retries."""


class VisionFatalError(Exception):
    """Request rejected (bad key, unknown model, bad request); retrying will not help."""


class ResponseParseError(ValueError):
    """The model answered, but not with the expected JSON list of points."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_points(text):
    """Parse a pointing answer into a list of Points; [] means nothing was found.

    Accepts bare JSON or JSON inside ``` / ```json fences, with or without text
    around the fence. Raises ResponseParseError on anything else.
    """
    if not text or not text.strip():
        raise ResponseParseError('empty response')
    fenced = _FENCE.search(text)
    body = fenced.group(1) if fenced else text.strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ResponseParseError(f'invalid JSON: {exc}') from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ResponseParseError(f'expected a JSON list, got {type(data).__name__}')

    points = []
    for item in data:
        if not isinstance(item, dict) or 'point' not in item:
            raise ResponseParseError(f'item without "point": {item!r}')
        point = item['point']
        if not (isinstance(point, (list, tuple)) and len(point) == 2
                and all(_is_number(c) for c in point)):
            raise ResponseParseError(f'"point" is not [y, x]: {point!r}')
        y, x = float(point[0]), float(point[1])
        if not (0.0 <= y <= 1000.0 and 0.0 <= x <= 1000.0):
            raise ResponseParseError(f'point outside 0-1000: {point!r}')
        points.append(Point(y=y, x=x, label=str(item.get('label', ''))))
    return points


def point_to_pixel(point, width, height):
    """Normalized [y, x] (0-1000) to integer pixel (u, v), clamped inside the image."""
    u = min(int(point.x / 1000.0 * width), width - 1)
    v = min(int(point.y / 1000.0 * height), height - 1)
    return u, v


class VisionPointer:
    """Locate a target in a JPEG using the OpenAI Responses API."""

    def __init__(self, model=DEFAULT_MODEL, reasoning_effort=DEFAULT_REASONING_EFFORT,
                 service_tier=DEFAULT_SERVICE_TIER, timeout=15.0, retries=1, api_key=None):
        try:
            import openai
        except ImportError as exc:
            raise VisionFatalError('Install the OpenAI SDK (see er_goto/README.md)') from exc

        api_key = api_key or os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise VisionFatalError('OPENAI_API_KEY is not set (see .env.example)')

        self._client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=retries)
        self._openai = openai
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._service_tier = service_tier

    def point(self, jpeg_bytes, target):
        """Return (points, raw_text), with an empty list when the target is absent."""
        jpeg = base64.b64encode(jpeg_bytes).decode('ascii')
        try:
            response = self._client.responses.create(
                model=self._model,
                service_tier=self._service_tier,
                reasoning={'effort': self._reasoning_effort},
                store=False,
                input=[{'role': 'user', 'content': [
                    {'type': 'input_text', 'text': PROMPT_TEMPLATE.format(target=target)},
                    {'type': 'input_image', 'image_url': f'data:image/jpeg;base64,{jpeg}'},
                ]}],
            )
        except self._openai.APIConnectionError as exc:
            raise VisionError(f'OpenAI connection failed: {exc}') from exc
        except self._openai.APIStatusError as exc:
            if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
                raise VisionError(f'OpenAI request failed: {exc}') from exc
            raise VisionFatalError(f'OpenAI rejected the request: {exc}') from exc

        if response.status != 'completed':
            raise VisionError(f'OpenAI response status: {response.status}')
        text = response.output_text
        return parse_points(text), text
