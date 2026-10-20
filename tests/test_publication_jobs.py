"""Publication identity and duplicate prevention with disposable material and fake workers."""
import copy
import unittest
from unittest.mock import patch
from tests.test_workbench_recovery import LocalWorkbench
from workbench_model import IDENTITY_FIELDS, validate_receipt, validate_publication_receipt
import draft_workbench as wb

class PublicationJobs(unittest.TestCase):
    setUp=LocalWorkbench.setUp
    tearDown=LocalWorkbench.tearDown
    start=LocalWorkbench.start
    run_worker=LocalWorkbench.run_worker
    receipt=LocalWorkbench.receipt

    def publication(self, expected, status='published_submission_verified'):
        publication={'status':status,'clicked':True,'evidence':{'kind':'documented_submit_response','submission_id':'fixture-submission'},
                     'observation':{'publish_requests':1,'publish_responses':1}}
        return {**{key:expected[key] for key in IDENTITY_FIELDS},'workflow_action':'publish','status':status,
                'verification':{key:True for key in ('upload_complete','fields','cover','media','account','submission')},
                'account':{'platform':expected['platform_id'],'identity':'actual-account-'+expected['platform_id'],'verified':True},
                'publication':publication}

    def test_saved_draft_does_not_satisfy_or_block_new_explicit_publication(self):
        draft,_=self.start();self.run_worker(draft)
        publication,thread=self.start(action='publish')
        thread.assert_called_once()
        self.assertEqual(publication['results'][0]['status'],'pending')
        expected=publication['results'][0]['expected']
        self.assertEqual(expected['workflow_action'],'publish')
        self.assertNotEqual(expected['operation_key'],draft['results'][0]['expected']['operation_key'])
        self.run_worker(publication,receipts=self.publication)
        self.assertEqual(publication['results'][0]['status'],'published_submission_verified')
        repeat,thread=self.start(action='publish');thread.assert_not_called()
        self.assertTrue(repeat['results'][0]['reused'])

    def test_attempted_unverified_submission_is_never_automatically_repeated(self):
        job,_=self.start(action='publish');self.run_worker(job,receipts=lambda e:self.publication(e,'published_unverified'))
        repeated,thread=self.start(action='publish');thread.assert_not_called()
        self.assertEqual(repeated['results'][0]['status'],'published_unverified')
        with self.assertRaises(ValueError):wb.retry_job({'expected_job_id':repeated['id'],'platform':'douyin','browser':'external','payload':self.payload,'previous_job_id':job['id']})

    def test_submission_receipt_needs_preparation_account_and_unique_acceptance(self):
        job,_=self.start(action='publish');expected=job['results'][0]['expected'];receipt=self.publication(expected)
        validate_publication_receipt(receipt,expected)
        with self.assertRaises(ValueError):validate_receipt(receipt,expected)
        for change in ('field','account','attempt','evidence','duplicate'):
            broken=copy.deepcopy(receipt)
            if change=='field':broken['verification']['fields']=False
            if change=='account':broken['account']['identity']='different-owner';expected['expected_account_identity']='actual-account-douyin'
            if change=='attempt':broken['receipt_nonce']='previous-attempt'
            if change=='evidence':broken['publication']['evidence']={}
            if change=='duplicate':broken['publication']['observation']['publish_requests']=2
            with self.subTest(change=change),self.assertRaises(ValueError):validate_publication_receipt(broken,expected)

if __name__=='__main__':unittest.main()
