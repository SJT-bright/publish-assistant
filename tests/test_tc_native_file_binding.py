"""Official DFS filename encoding must retain exact native File identity.

Public source: tc-content-841.d575b04c.js, getUploadHeader at offsets
862023/2308873 encodes file.name once; initTaskId at 870997/2314678
passes that header unchanged. No browser, account, upload or network is used.
"""
import hashlib
import unittest
from urllib.parse import quote, unquote

from uploader.draft_evidence import DraftEvidenceSession, _tc_metadata, _tc_query


DATA = b'isolated native-file fixture'
DIGEST = hashlib.sha256(DATA).hexdigest()


def arguments(name):
    # encodeURIComponent's literal-safe set, rather than query double-encoding.
    encoded = quote(name, safe="~()*!.'-")
    return f'filekey={encoded}&filesize={len(DATA)}&taskid=isolated-fixture'


def selected_session(name, digest=DIGEST):
    session = DraftEvidenceSession.__new__(DraftEvidenceSession)
    session.started_at = 0
    session.files = [{'name': name, 'size': len(DATA), 'sha256': digest,
                      'selected_at': 1, 'type': 'video/mp4'}]
    session.assets = {'video': {'name': name, 'size': len(DATA),
                                'sha256': DIGEST, 'kind': 'video'}}
    return session


class NativeFileBindingTests(unittest.TestCase):
    def test_unicode_plus_percent_and_literal_escape_bind_after_one_decode(self):
        name = '中文 + 100% 字面%2F.mp4'
        metadata = _tc_metadata(_tc_query(arguments(name)), 'filesize')
        self.assertEqual(metadata, (name, len(DATA)))
        session = selected_session(name)
        self.assertEqual(session._tc_selected_asset({'metadata': metadata, 'observed_at': 2}),
                         (session.files[0], 'video'))

    def test_double_decode_corrupts_a_valid_literal_escape_and_must_not_bind(self):
        name = 'literal%2Fname%25.mp4'
        metadata = _tc_metadata(_tc_query(arguments(name)), 'filesize')
        self.assertEqual(metadata[0], name)
        wrong_name = unquote(metadata[0])
        self.assertNotEqual(wrong_name, name)
        self.assertIsNone(selected_session(name)._tc_selected_asset(
            {'metadata': (wrong_name, len(DATA)), 'observed_at': 2}))

    def test_double_encoded_alias_is_not_guessed_to_be_the_selected_file(self):
        name = '原始 + %2F.mp4'
        once_encoded = quote(name, safe="~()*!.'-")
        metadata = _tc_metadata(_tc_query(arguments(once_encoded)), 'filesize')
        self.assertEqual(metadata[0], once_encoded)
        self.assertNotEqual(metadata[0], name)
        self.assertIsNone(selected_session(name)._tc_selected_asset(
            {'metadata': metadata, 'observed_at': 2}))

    def test_partial_md5_cannot_replace_native_full_sha256(self):
        name = 'fixture.mp4'
        metadata = _tc_metadata(_tc_query(arguments(name)), 'filesize')
        partial_md5 = hashlib.md5(DATA[:5], usedforsecurity=False).hexdigest()
        self.assertIsNone(selected_session(name, partial_md5)._tc_selected_asset(
            {'metadata': metadata, 'observed_at': 2}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
