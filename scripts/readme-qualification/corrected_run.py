"""One exact corrected README target, without an injected control stylesheet."""
import sys
sys.dont_write_bytecode = True
import signal
from pathlib import Path
import run as original


def run(root, out):
    return original.run(root, out, case='corrected')


if __name__ == '__main__':
    def interrupted(signum, _frame):
        raise InterruptedError(f'supervisor signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
