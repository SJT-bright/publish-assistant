import copy
import unittest
from workbench_model import canonical_sha, platform_content_fields
from uploader.draft_evidence import _checkpoint_expected_identity, CheckpointError

class CheckpointWorkflowIntentTests(unittest.TestCase):
    def payload(self, action):
        fields={'title':'Fixture title','caption':'Fixture body','description':'Fixture body','tags':[]}
        value={'platform_id':'douyin','job_id':'fixture-job','receipt_nonce':'fixture-nonce',
               'account_key':'fixture-account','video_sha256':'a'*64,
               'content_sha256':canonical_sha(platform_content_fields('douyin',fields)),
               'cover_sha256':{},'platform_fields':fields,'workflow_action':action}
        fingerprint={k:value[k] for k in ('platform_id','account_key','video_sha256','content_sha256','cover_sha256')}
        if action=='publish':fingerprint['workflow_action']='publish'
        value['operation_key']=canonical_sha(fingerprint)
        return value
    def test_publishing_checkpoint_uses_the_same_intent_fingerprint_as_job(self):
        value=self.payload('publish')
        self.assertEqual(_checkpoint_expected_identity(value,'douyin')['operation_key'],value['operation_key'])
    def test_draft_checkpoint_remains_compatible(self):
        value=self.payload('draft')
        self.assertEqual(_checkpoint_expected_identity(value,'douyin')['operation_key'],value['operation_key'])
    def test_draft_and_publish_checkpoint_identities_cannot_cross(self):
        for original,changed in [('draft','publish'),('publish','draft')]:
            value=copy.deepcopy(self.payload(original));value['workflow_action']=changed
            with self.assertRaisesRegex(CheckpointError,'checkpoint_operation_mismatch'):
                _checkpoint_expected_identity(value,'douyin')
