"""Source-bound intake hints; lexical cues are not verified issue classifications."""
import hashlib
import re


FIELDS = {
    'observed_behavior': r'actual|observed|fails?|error|exception|traceback|crash|lỗi|thực tế|hiện tại',
    'expected_behavior': r'expected|expectation|should|mong đợi|kỳ vọng|đáng lẽ',
    'reproduction': r'reproduc|steps to|```|tái hiện|các bước|ví dụ tối thiểu',
    'environment': r'version|python\s*\d|platform|environment|operating system|phiên bản|môi trường|hệ điều hành',
}
INTENTS = {
    'bug_report': r'\b(?:bug|fails?|error|exception|traceback|crash)\b|\blỗi\b',
    'feature_request': r'feature request|enhancement|add support|new feature|tính năng mới|bổ sung tính năng',
    'question': r'how (?:do|can|to)|usage question|làm (?:thế nào|sao)|cách sử dụng',
}


def inspect_issue(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 120000:
        raise ValueError('A bounded issue description is required')
    lines = text.splitlines()
    def matches(pattern):
        return [{'line': number, 'text': line[:400], 'truncated': len(line)>400,
                 'source_line_sha256': hashlib.sha256(line.encode()).hexdigest(),
                 'text_sha256': hashlib.sha256(line[:400].encode()).hexdigest()}
                for number, line in enumerate(lines, 1) if re.search(pattern, line, re.I)][:5]
    fields = {key: {'status': 'mentioned' if (found := matches(pattern)) else 'not_found',
                    'sources': found} for key, pattern in FIELDS.items()}
    signals = {key: matches(pattern) for key, pattern in INTENTS.items()}
    found = [key for key, value in signals.items() if value]
    return {'schema': 'issue_intake_v1', 'query_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'intent': found[0] if len(found) == 1 else 'unknown',
            'intent_signals': signals, 'classification': 'lexical_hint_unverified',
            'fields': fields, 'missing_information': [key for key, value in fields.items() if value['status']=='not_found'],
            'limitations': ['A matching word or heading does not establish completeness or causal truth.',
                            'Absence of a cue is a request to review, not proof that information is absent.']}
