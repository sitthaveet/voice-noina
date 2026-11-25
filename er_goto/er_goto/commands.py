# SPDX-License-Identifier: GPL-3.0-only
"""Command dispatch shared by the terminal and voice modes."""

from typing import NamedTuple, Optional

EXIT_OK = 0
EXIT_FAILED = 1
WALK_ACTIONS = ('go', 'go_and_wave')


class Command(NamedTuple):
    action: str
    target: Optional[str]
    offset: Optional[int] = None  # originating GPT-Live delegation time


class NewCommand(Exception):
    """Interrupt an active walk so the control loop can process another command."""

    def __init__(self, command):
        super().__init__(command)
        self.command = command


def _walk(node, command, get_command, report):
    greeting = command if command.action == 'go_and_wave' else None

    def poll():
        nonlocal greeting
        incoming = get_command()
        if incoming and incoming.action == 'wave':
            greeting = incoming._replace(target=command.target)
            return None
        return incoming

    try:
        node.cancel_wave()
        code = node.run(command.target, poll)
    except NewCommand:
        raise
    except Exception as exc:
        node.get_logger().error(f'{type(exc).__name__}: {exc}')
        code = EXIT_FAILED
    finally:
        node.publish_zero(count=10)

    # A stop or replacement can arrive while the final zeroes are being sent.
    incoming = poll() if command.offset is not None else None
    if incoming:
        raise NewCommand(incoming)
    if code != EXIT_OK:
        status = 'failed'
    elif greeting is None:
        status = 'arrived'
    else:
        try:
            status = node.start_wave()
        except Exception as exc:
            node.get_logger().error(f'Arrived but could not wave: {exc}')
            status = 'arrived, wave failed'
    report(greeting or command, status)


def command_loop(node, get_command, report=lambda command, status: None, pending=None, help_text=None):
    log = node.get_logger()
    while True:
        command = pending or get_command(0.25)
        pending = None
        if command is None:
            continue
        try:
            if command.action == 'quit':
                return EXIT_OK
            if command.action == 'help':
                log.info(help_text)
            elif command.action == 'stop':
                node.publish_zero()
                node.cancel_wave()
                log.info('Stopped. Waiting for the next command')
                report(command, 'stopped')
            elif command.action == 'wave':
                node.publish_zero()
                pending = get_command()
                if pending is None:
                    report(command, node.start_wave())
            elif command.action in WALK_ACTIONS:
                _walk(node, command, get_command, report)
                log.info('Waiting for the next command')
        except NewCommand as exc:
            log.info(f'Leaving "{command.target}" for a new command')
            pending = exc.command
        except Exception as exc:
            log.error(f'{type(exc).__name__}: {exc}')
            report(command, 'failed')
