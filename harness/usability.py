"""Validate a five-task usability capture without inferring human success or adoption."""

import argparse
import hashlib
import json
import re
import sys

from harness import authoring, trials


PROTOCOL_FORMAT = 'sandbox-usability-protocol-1'
CAPTURE_FORMAT = 'sandbox-usability-capture-1'
REPORT_FORMAT = 'sandbox-usability-report-1'
GROUPS = {'first-time', 'experienced'}
OUTCOMES = {'completed', 'partial', 'failed', 'not-run'}
SOURCES = {'synthetic', 'human-observation'}
ARTIFACT_KINDS = {'notes', 'screen-recording', 'receipt'}
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
SHA = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'[0-9a-f]{40,64}')


class UsabilityError(ValueError):
    """The capture is incomplete or inconsistent with the frozen protocol."""


def require(condition, message):
    if not condition:
        raise UsabilityError(message)


def fields(value, expected, label):
    require(isinstance(value, dict) and set(value) == expected, f'{label} fields are invalid')


def bounded_text(value, label, maximum=240):
    require(isinstance(value, str) and 1 <= len(value.strip()) <= maximum and not any(
        ord(char) < 32 for char in value
    ), f'{label} must be 1-{maximum} plain characters')


def validate_protocol(protocol):
    fields(protocol, {'format', 'target', 'participant_groups', 'tasks'}, 'protocol')
    require(protocol['format'] == PROTOCOL_FORMAT and protocol['target'] == 'deep-ai',
            'unsupported usability protocol')
    require(protocol['participant_groups'] == ['first-time', 'experienced'],
            'protocol must cover first-time and experienced users')
    tasks = protocol['tasks']
    require(isinstance(tasks, list) and len(tasks) == 5, 'protocol needs exactly five tasks')
    ids = []
    for task in tasks:
        fields(task, {'id', 'instruction', 'success_observation', 'send_question'}, 'task')
        require(isinstance(task['id'], str) and ID.fullmatch(task['id']), 'invalid task id')
        ids.append(task['id'])
        for name in ('instruction', 'success_observation', 'send_question'):
            bounded_text(task[name], name, 500)
    require(len(set(ids)) == 5, 'task ids must be distinct')
    return ids


def evaluate(protocol, capture):
    """Return observed counts and preserved task records, never a usability score."""
    ids = validate_protocol(protocol)
    fields(capture, {'format', 'protocol_sha256', 'session_id', 'participant_group',
                     'source', 'app_revision', 'observations'}, 'capture')
    digest = hashlib.sha256(trials.canonical(protocol)).hexdigest()
    require(capture['format'] == CAPTURE_FORMAT and capture['protocol_sha256'] == digest,
            'capture does not match the frozen protocol')
    require(isinstance(capture['session_id'], str) and ID.fullmatch(capture['session_id']),
            'invalid anonymous session id')
    require(isinstance(capture['participant_group'], str) and capture['participant_group'] in GROUPS
            and isinstance(capture['source'], str) and capture['source'] in SOURCES,
            'invalid participant group or observation source')
    require(isinstance(capture['app_revision'], str) and REVISION.fullmatch(capture['app_revision']),
            'app revision must be an exact source commit')
    if capture['source'] == 'human-observation':
        require(set(capture['app_revision']) != {'0'}, 'human observation needs an actual app revision')
    rows = capture['observations']
    require(isinstance(rows, list) and len(rows) == 5, 'capture needs five task observations')
    counts = {outcome: 0 for outcome in sorted(OUTCOMES)}
    wrong_turns = 0
    for expected_id, row in zip(ids, rows):
        fields(row, {'task_id', 'outcome', 'wrong_turns', 'belief_about_sent', 'artifacts'},
               'observation')
        require(row['task_id'] == expected_id and isinstance(row['outcome'], str)
                and row['outcome'] in OUTCOMES,
                'observation order or outcome differs from protocol')
        turns = row['wrong_turns']
        require(isinstance(turns, list) and len(turns) <= 20, 'wrong turns exceed the task bound')
        for turn in turns:
            bounded_text(turn, 'wrong turn')
        belief = row['belief_about_sent']
        require(belief is None or isinstance(belief, str), 'belief must be text or null')
        if belief is not None:
            bounded_text(belief, 'belief about sent content')
        if row['outcome'] == 'not-run':
            require(not turns and belief is None, 'not-run task cannot claim observations')
        elif capture['source'] == 'human-observation':
            require(belief is not None, 'human observation must record what the user believed was sent')
        artifacts = row['artifacts']
        require(isinstance(artifacts, list) and len(artifacts) <= 10, 'artifact list exceeds the task bound')
        if row['outcome'] == 'not-run':
            require(not artifacts, 'not-run task cannot claim artifacts')
        elif capture['source'] == 'human-observation':
            require(artifacts, 'human observation needs a retained artifact reference')
        for artifact in artifacts:
            fields(artifact, {'kind', 'sha256'}, 'artifact')
            require(isinstance(artifact['kind'], str) and artifact['kind'] in ARTIFACT_KINDS
                    and isinstance(artifact['sha256'], str)
                    and SHA.fullmatch(artifact['sha256']), 'artifact reference is invalid')
        counts[row['outcome']] += 1
        wrong_turns += len(turns)
    return {
        'format': REPORT_FORMAT,
        'protocol_sha256': digest,
        'capture_sha256': hashlib.sha256(trials.canonical(capture)).hexdigest(),
        'session_id': capture['session_id'],
        'participant_group': capture['participant_group'],
        'source': capture['source'],
        'app_revision': capture['app_revision'],
        'counts': counts,
        'wrong_turns': wrong_turns,
        'observations': rows,
        'qualifications': [
            'Participant group, human presence, task outcomes and artifact ownership are caller-reported.',
            'A digest binds supplied bytes but does not authenticate a person or prove what the app sent.',
            'Completion and wrong turns are observations, not a general usability or visual quality score.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--capture')
    choice.add_argument('--digest', action='store_true', help='print the canonical protocol SHA-256')
    args = parser.parse_args(argv)
    try:
        protocol = json.loads(authoring.read_bounded(args.protocol))
        validate_protocol(protocol)
        if args.digest:
            print(hashlib.sha256(trials.canonical(protocol)).hexdigest())
            return 0
        capture = json.loads(authoring.read_bounded(args.capture))
        result = evaluate(protocol, capture)
    except (ValueError, authoring.AuthoringError) as exc:
        parser.exit(2, f'usability: {exc}\n')
    sys.stdout.buffer.write(trials.canonical(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
