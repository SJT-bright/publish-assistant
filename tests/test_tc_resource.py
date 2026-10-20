"""Synthetic correlation checks; no actual upload or account operation."""

import unittest
from urllib.parse import urlencode

from uploader.tc_resource import tc_resource_identity
from uploader.draft_evidence import parse_record


PATH = '/251/20302/stodownload'
PLAIN_PATH = '/251/20304/stodownload'
ENCRYPTED = {
    'encfilekey': 'opaque-encrypted-file-A', 'm': '',
    'uzid': 'opaque-resource-A', 'bizid': '1023', 'hy': 'SZ', 'idx': '1',
    'dotrans': '0', 'scene': '1',
}
PLAIN = {
    'filekey': 'opaque-file-key-A', 'm': 'shared-media-field',
    'storeid': 'opaque-store-A', 'bizid': '1023', 'hy': 'SZ',
    'dotrans': '0', 'adaptivelytrans': '0',
}
IMAGE_COMMON = {
    'encfilekey': 'opaque-image-file-A', 'm': '', 'uzid': 'opaque-image-resource-A',
    'hy': 'SZ', 'idx': '1', 'picformat': '123',
}
IMAGE_COVER = dict(IMAGE_COMMON, scene='1')
IMAGE_THUMBNAIL = dict(IMAGE_COMMON, bizid='1023', dotrans='0')


def resource(fields=None, *, origin='https://finder.video.qq.com', path=PATH):
    return origin + path + '?' + urlencode(ENCRYPTED if fields is None else fields)


def image_resource(fields, *, origin='https://finder.video.qq.com'):
    return resource(fields, origin=origin, path=PLAIN_PATH)


class TCResourceIdentityTests(unittest.TestCase):
    def test_official_host_replacement_and_query_order_preserve_identity(self):
        first = tc_resource_identity(resource())
        second = tc_resource_identity(resource(dict(reversed(list(ENCRYPTED.items()))),
                                               origin='http://wxapp.tc.qq.com'))
        self.assertRegex(first, r'^tc-resource:[0-9a-f]{64}$')
        self.assertEqual(first, second)

    def test_auth_fields_are_never_retained_and_rotation_does_not_change_identity(self):
        expected = tc_resource_identity(resource())
        for key in ('token', 'sign', 'findertoken'):
            with self.subTest(key=key):
                secret = 'synthetic-auth-private-A'
                result = tc_resource_identity(resource(dict(ENCRYPTED, **{key: secret})))
                self.assertEqual(result, expected)
                self.assertNotIn(secret, result)
                self.assertNotIn(key, result)
                self.assertEqual(result, tc_resource_identity(resource(
                    dict(ENCRYPTED, **{key: 'synthetic-auth-private-B'}))))

    def test_all_non_auth_fields_and_presence_participate(self):
        expected = tc_resource_identity(resource())
        for key in ENCRYPTED:
            with self.subTest(key=key):
                changed = dict(ENCRYPTED)
                changed[key] += '-different'
                self.assertNotEqual(expected, tc_resource_identity(resource(changed)))
        for key in ('scene', 'dotrans'):
            reduced = dict(ENCRYPTED)
            del reduced[key]
            self.assertIsNotNone(tc_resource_identity(resource(reduced)))
            self.assertNotEqual(expected, tc_resource_identity(resource(reduced)))
        with_basedata = dict(ENCRYPTED, basedata='opaque-unclassified-context')
        self.assertNotEqual(expected, tc_resource_identity(resource(with_basedata)))

    def test_shared_m_does_not_merge_distinct_resources(self):
        changed = dict(ENCRYPTED, encfilekey='opaque-encrypted-file-B', uzid='opaque-resource-B')
        self.assertEqual(changed['m'], ENCRYPTED['m'])
        self.assertNotEqual(tc_resource_identity(resource()), tc_resource_identity(resource(changed)))

    def test_missing_opaque_identity_and_mixed_formats_are_rejected(self):
        for key in ('encfilekey', 'm', 'uzid', 'bizid', 'hy', 'idx'):
            reduced = dict(ENCRYPTED)
            del reduced[key]
            with self.subTest(key=key):
                self.assertIsNone(tc_resource_identity(resource(reduced)))
        for fields in [dict(ENCRYPTED, filekey='legacy'), dict(ENCRYPTED, storeid='legacy'),
                       dict(PLAIN, encfilekey='encrypted'), {'m': 'shared-media-field'}]:
            self.assertIsNone(tc_resource_identity(resource(fields)))
        for key in ('encfilekey', 'uzid', 'bizid', 'hy', 'idx'):
            self.assertIsNone(tc_resource_identity(resource(dict(ENCRYPTED, **{key: ''}))))

    def test_processed_scene_family_keeps_every_opaque_field_and_distinct_identity(self):
        fields={'encfilekey':'scene-resource','m':'','uzid':'scene-id','hy':'SZ','idx':'1',
                'scene':'1','basedata':'scene-context'}
        identity=tc_resource_identity(resource(fields))
        self.assertIsNotNone(identity)
        self.assertNotEqual(identity,tc_resource_identity(resource()))
        for key in fields:
            with self.subTest(key=key):
                changed={**fields,key:fields[key]+'changed'}
                self.assertNotEqual(identity,tc_resource_identity(resource(changed)))
                missing=dict(fields);del missing[key]
                self.assertIsNone(tc_resource_identity(resource(missing)))
        self.assertIsNone(tc_resource_identity(resource(dict(fields,extra='unknown'))))
        self.assertEqual(identity,tc_resource_identity(resource(dict(fields,token='rotated-auth'))))

    def test_older_official_filekey_storeid_family_is_separate(self):
        result = tc_resource_identity(resource(PLAIN, path=PLAIN_PATH))
        self.assertIsNotNone(result)
        self.assertEqual(result, tc_resource_identity(resource(
            PLAIN, origin='http://wxapp.tc.qq.com', path=PLAIN_PATH)))
        self.assertNotEqual(result, tc_resource_identity(resource()))
        self.assertIsNone(tc_resource_identity(resource(PLAIN)))
        self.assertNotEqual(tc_resource_identity(resource(path=PLAIN_PATH)), tc_resource_identity(resource()))
        self.assertIsNone(tc_resource_identity(resource(dict(PLAIN, m=''), path=PLAIN_PATH)))
        for key in ('filekey', 'storeid'):
            changed = dict(PLAIN)
            changed[key] += '-B'
            self.assertNotEqual(result, tc_resource_identity(resource(changed, path=PLAIN_PATH)))

    def test_native_uploaded_image_shape_is_path_sensitive_and_keeps_all_opaque_fields(self):
        fields=dict(ENCRYPTED)
        image=image_resource(fields)
        identity=tc_resource_identity(image)
        self.assertRegex(identity,r'^tc-resource:[0-9a-f]{64}$')
        self.assertEqual(identity,tc_resource_identity(image_resource(dict(reversed(list(fields.items()))),origin='http://wxapp.tc.qq.com')))
        self.assertEqual(identity,tc_resource_identity(image_resource(dict(fields,token='rotated',sign='rotated-sign'))))
        self.assertNotEqual(identity,tc_resource_identity(resource(fields)))
        self.assertNotEqual(identity,tc_resource_identity(image_resource(IMAGE_COVER)))
        for key in fields:
            with self.subTest(key=key):
                changed={**fields,key:fields[key]+'different'}
                self.assertNotEqual(identity,tc_resource_identity(image_resource(changed)))
                missing=dict(fields);del missing[key]
                self.assertIsNone(tc_resource_identity(image_resource(missing)))
        for changed in (dict(fields,picformat='123'),dict(fields,basedata='extra'),dict(fields,filekey='other'),
                dict(fields,storeid='other'),dict(fields,unknown='other')):
            self.assertIsNone(tc_resource_identity(image_resource(changed)))
        for path in ('/251/20304/stodownload/','/251/20305/stodownload','/20304/stodownload'):
            self.assertIsNone(tc_resource_identity(resource(fields,path=path)))
        self.assertIsNone(tc_resource_identity(image+'&scene=duplicate'))
        self.assertIsNone(tc_resource_identity(image.replace('encfilekey=','%65ncfilekey=')))

    def test_native_uploaded_covers_http_finish_and_saved_slots_share_one_canonical_id(self):
        from uploader.draft_evidence import _tc_resource_aliases
        portrait=image_resource(ENCRYPTED)
        landscape=image_resource(dict(ENCRYPTED,encfilekey='different-landscape',uzid='different-image'))
        parsed=parse_record('tencent',{'objectId':'fixture-id','postReq':{'objectDesc':{
            'description':'fixture caption','media':[{'url':resource(),'coverUrl':portrait,'shareCoverUrl':landscape,'thumbUrl':portrait}]}}})
        for slot,url in (('cover_portrait',portrait),('cover_landscape',landscape)):
            self.assertEqual(parsed['cover_slots'][slot],_tc_resource_aliases(url))
            self.assertEqual(parsed['cover_slots'][slot],_tc_resource_aliases(url.replace('https://finder.video.qq.com','http://wxapp.tc.qq.com')))
            self.assertEqual(len(parsed['cover_slots'][slot]),1)

    def test_official_image_cover_and_thumbnail_shapes_are_supported(self):
        for fields in (IMAGE_COVER, IMAGE_THUMBNAIL):
            with self.subTest(shape=sorted(fields)):
                first = tc_resource_identity(image_resource(fields))
                reordered = dict(reversed(list(fields.items())))
                second = tc_resource_identity(image_resource(
                    dict(reordered, token='synthetic-auth-A'), origin='http://wxapp.tc.qq.com'))
                self.assertRegex(first, r'^tc-resource:[0-9a-f]{64}$')
                self.assertEqual(first, second)

    def test_image_identity_retains_every_non_auth_field_and_value(self):
        for shape in (IMAGE_COVER, IMAGE_THUMBNAIL):
            expected = tc_resource_identity(image_resource(shape))
            for key, value in shape.items():
                changed_value = '124' if key == 'picformat' else value + '-changed'
                changed = dict(shape, **{key: changed_value})
                with self.subTest(key=key, shape=sorted(shape)):
                    self.assertNotEqual(expected, tc_resource_identity(image_resource(changed)))

    def test_image_families_require_the_complete_exact_observed_field_set(self):
        for shape in (IMAGE_COVER, IMAGE_THUMBNAIL):
            required = set(shape)
            for key in required:
                incomplete = dict(shape)
                del incomplete[key]
                with self.subTest(missing=key, shape=sorted(shape)):
                    self.assertIsNone(tc_resource_identity(image_resource(incomplete)))
        for fields in (dict(IMAGE_COVER, bizid='1023'),
                       dict(IMAGE_COVER, dotrans='0'),
                       dict(IMAGE_THUMBNAIL, scene='1'),
                       dict(IMAGE_COMMON, scene='1', bizid='1023', dotrans='0'),
                       dict(IMAGE_COVER, unknown='opaque'),
                       dict(IMAGE_THUMBNAIL, picformat='')):
            with self.subTest(fields=sorted(fields)):
                self.assertIsNone(tc_resource_identity(image_resource(fields)))
        self.assertIsNone(tc_resource_identity(image_resource(IMAGE_COVER).replace(PLAIN_PATH, PATH)))
        for malformed in (
                image_resource(IMAGE_COVER) + '&picformat=124',
                image_resource(IMAGE_THUMBNAIL) + '&token=A&token=B',
                image_resource(IMAGE_COVER).replace('picformat=123', '%70icformat=123'),
                image_resource(IMAGE_COVER).replace('picformat=123', 'picformat='),
                image_resource(IMAGE_COVER).replace('picformat=123', 'picformat=12%203'),
                image_resource(IMAGE_COVER).replace('picformat=123', 'picformat=%C3%A9')):
            with self.subTest(malformed=malformed.split('?', 1)[-1]):
                self.assertIsNone(tc_resource_identity(malformed))

    def test_saved_tencent_record_canonicalizes_image_cover_resources(self):
        video = resource()
        thumbnail = image_resource(IMAGE_THUMBNAIL)
        portrait = image_resource(IMAGE_COVER)
        landscape = image_resource(dict(IMAGE_COVER, encfilekey='opaque-image-file-B',
                                        uzid='opaque-image-resource-B'))
        parsed = parse_record('tencent', {
            'objectId': 'fixture-draft', 'finderUsername': 'fixture-owner',
            'postReq': {'objectDesc': {'description': 'fixture caption', 'media': [{
                'url': video, 'thumbUrl': thumbnail, 'coverUrl': portrait,
                'shareCoverUrl': landscape,
            }]}}
        })
        self.assertEqual(parsed['video_ids'], [tc_resource_identity(video)])
        self.assertEqual(parsed['cover_slots'], {
            'cover_portrait': [tc_resource_identity(portrait)],
            'cover_landscape': [tc_resource_identity(landscape)],
        })
        self.assertEqual(parsed['cover_ids'], sorted(map(tc_resource_identity,
                                                         (thumbnail, portrait, landscape))))

    def test_duplicate_unknown_encoded_key_and_case_aliases_are_rejected(self):
        url = resource()
        for tail in ['&m=duplicate', '&token=A&token=B', '&%6d=duplicate',
                     '&%65ncfilekey=duplicate', '&unknown=A', '&Token=A',
                     '&scene=', '&scene', '&', '&idx=1;idx=2']:
            with self.subTest(tail=tail):
                self.assertIsNone(tc_resource_identity(url + tail))
        self.assertIsNone(tc_resource_identity(url.replace('encfilekey=', '%65ncfilekey=', 1)))

    def test_values_are_decoded_exactly_once_and_invalid_encoding_fails(self):
        literal_escape = dict(ENCRYPTED, encfilekey='opaque%2Fresource%25+A')
        decoded_twice = dict(ENCRYPTED, encfilekey='opaque/resource%+A')
        self.assertIsNotNone(tc_resource_identity(resource(literal_escape)))
        self.assertNotEqual(tc_resource_identity(resource(literal_escape)),
                            tc_resource_identity(resource(decoded_twice)))
        self.assertEqual(tc_resource_identity(resource(dict(ENCRYPTED, encfilekey='opaque+A'))),
                         tc_resource_identity(resource().replace(
                             'encfilekey=opaque-encrypted-file-A', 'encfilekey=opaque%2bA')))
        for encoded in ['%', '%2', '%GG', '%FF', '%00', '%20', 'plain+space', '%0A']:
            malformed = resource().replace('encfilekey=opaque-encrypted-file-A', 'encfilekey=' + encoded)
            with self.subTest(encoded=encoded):
                self.assertIsNone(tc_resource_identity(malformed))

    def test_other_origins_ports_credentials_and_unobserved_scheme_pairs_fail(self):
        for origin in ['https://finder.video.qq.com:443', 'http://wxapp.tc.qq.com:80',
                       'https://user:pass@finder.video.qq.com', 'https://finder.video.qq.com.evil.test',
                       'https://other.video.qq.com', 'https://wxapp.tc.qq.com',
                       'http://finder.video.qq.com', 'ftp://finder.video.qq.com',
                       'https://FINDER.video.qq.com', 'https://finder.video.qq.com.',
                       '//finder.video.qq.com']:
            with self.subTest(origin=origin):
                self.assertIsNone(tc_resource_identity(resource(origin=origin)))

    def test_path_fragment_control_and_oversized_inputs_fail(self):
        for path in ['/download', PATH + '/', '/251/20302/%73todownload', '/different']:
            self.assertIsNone(tc_resource_identity(resource(path=path)))
        for value in [resource() + '#', resource() + '#fragment', ' ' + resource(),
                      resource() + '\n', resource() + '\\', resource() + '中',
                      resource(dict(ENCRYPTED, basedata='X' * 4097)), 'X' * 8193,
                      None, b'https://finder.video.qq.com', '', 'tc-resource:' + 'a' * 64]:
            with self.subTest(kind=type(value).__name__):
                self.assertIsNone(tc_resource_identity(value))


if __name__ == '__main__':
    unittest.main()
