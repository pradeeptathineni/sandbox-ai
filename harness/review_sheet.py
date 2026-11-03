"""Render only the existing frozen blind-session projection as an offline sheet."""
from pathlib import Path
import json
import re

from .grounding_review import RUBRIC


def render(session):
    """Keep reference labels and provenance out of the downloadable reviewer file."""
    if (not isinstance(session, dict) or
            set(session) != {'anchor_set_sha256', 'rubric', 'view', 'reference_ratings_withheld'} or
            session['reference_ratings_withheld'] is not True or session['rubric'] != RUBRIC or
            not isinstance(session['anchor_set_sha256'], str) or
            not re.fullmatch(r'[a-f0-9]{64}', session['anchor_set_sha256'])):
        raise ValueError('review sheet requires the exact withheld-reference blind session')
    fields = {'armAlias', 'questionId', 'question', 'answer', 'evidenceExcerpts', 'answerStatus', 'availability'}
    if (not isinstance(session['view'], list) or not 1 <= len(session['view']) <= 96 or
            any(not isinstance(row, dict) or set(row) != fields for row in session['view'])):
        raise ValueError('review sheet requires the closed blind row projection')
    # Source text stays data even when it contains HTML/script closing tags.
    data = json.dumps(session, ensure_ascii=True, separators=(',', ':'))
    data = data.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    template = Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
    return template.replace('__SANDBOX_BLIND_SESSION__', data)
