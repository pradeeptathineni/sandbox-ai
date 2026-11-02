"""Port-free SB07 CLI for classifying caller-supplied lifecycle observations."""
import argparse
import json
import sys
from pathlib import Path

from . import lifecycle_recovery, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path,
                        help='caller-supplied sandbox-lifecycle-observations-1 JSON')
    args = parser.parse_args(argv)
    try:
        observations = json.loads(args.input.read_bytes())
        report = lifecycle_recovery.analyze(observations)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(trials.canonical(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
