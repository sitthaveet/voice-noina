# SPDX-License-Identifier: GPL-3.0-only
"""Command dispatch shared by the terminal and voice modes."""

from typing import NamedTuple, Optional

EXIT_OK = 0
EXIT_FAILED = 1


class Command(NamedTuple):
    action: str
    target: Optional[str]
    offset: Optional[int] = None  # originating GPT-Live delegation time


class NewCommand(Exception):
    """Interrupt an active walk so the control loop can process another command."""

    def __init__(self, command):
        super().__init__(command)
        self.command = command


def command_loop(node, get_command, report=lambda command, status: None, pending=None, help_text=None):
    log = node.get_logger()
    while True:
        command = pending or get_command(0.25)
        pending = None
        if command is None:
            continue
        action, target = command.action, command.target
        if action == 'quit':
            return EXIT_OK
        if action == 'help':
            log.info(help_text)
        elif action == 'stop':
            node.publish_zero()
            log.info('Stopped. Waiting for the next command')
            report(command, 'stopped')
        elif action == 'go':
            try:
                code = node.run(target, get_command)
            except NewCommand as exc:
                log.info(f'Leaving "{target}" for a new command')
                pending = exc.command
            except Exception as exc:
                log.error(f'{type(exc).__name__}: {exc}')
                code = EXIT_FAILED
            finally:
                node.publish_zero(count=10)
            if pending is None:
                report(command, 'arrived' if code == EXIT_OK else 'failed')
                log.info('Waiting for the next command')
