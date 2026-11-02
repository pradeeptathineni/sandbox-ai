"""Port-free SB07 CLI for classifying caller-supplied lifecycle observations."""
import argparse
import json
import sys
from pathlib import Path

from . import lifecycle_recovery, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path,
                        help='legacy observations or sandbox-lifecycle-owner-snapshot-1 JSON')
    parser.add_argument('--store', type=Path,
                        help='trial store for owner snapshots; required only for owner-backed input')
    args = parser.parse_args(argv)
    try:
        observations = json.loads(args.input.read_bytes())
        if isinstance(observations, dict) and observations.get('format') == lifecycle_recovery.OWNER_FORMAT:
            if args.store is None:
                parser.error('--store is required for owner-backed input')
            report = lifecycle_recovery.analyze_owner_snapshots(args.store, observations)
        else:
            if args.store is not None:
                parser.error('--store applies only to owner-backed input')
            report = lifecycle_recovery.analyze(observations)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(trials.canonical(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
