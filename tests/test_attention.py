import unittest

from attention import update_attention, is_unread, mark_viewed


def result(turn, status='completed'):
    return {'status':status,'answer_turn_id':turn,'turn_id':turn,
            'answer':'Private answer','event_time':turn}


class AttentionTests(unittest.TestCase):
    def test_first_historical_result_is_baseline_then_new_turn_is_unread(self):
        saved={}
        self.assertTrue(update_attention(saved,'a',result('one')))
        self.assertFalse(is_unread(saved,'a'))
        self.assertTrue(update_attention(saved,'a',result('two')))
        self.assertTrue(is_unread(saved,'a'))
        self.assertNotIn('Private answer',str(saved))
        mark_viewed(saved,'a')
        self.assertFalse(is_unread(saved,'a'))
        self.assertFalse(update_attention(saved,'a',result('two')))
        self.assertFalse(is_unread(saved,'a'))

    def test_first_observed_running_turn_becomes_unread_on_completion(self):
        saved={}
        update_attention(saved,'a',{'status':'unknown'})
        update_attention(saved,'a',{'status':'active','turn_id':'one'})
        update_attention(saved,'a',result('one'))
        self.assertTrue(is_unread(saved,'a'))

    def test_unread_survives_restart_next_turn_and_temporary_missing_log(self):
        saved={}
        update_attention(saved,'a',result('one'))
        update_attention(saved,'a',result('two'))
        update_attention(saved,'a',dict(result('two','active'),turn_id='three',event_time='later'))
        update_attention(saved,'a',{'status':'unknown'})
        self.assertTrue(is_unread(saved,'a'))
        mark_viewed(saved,'a')
        mark_viewed(saved,'a',unread=True)
        update_attention(saved,'a',result('two'))
        self.assertTrue(is_unread(saved,'a'))

    def test_answerless_completion_is_identified_by_turn(self):
        saved={}
        update_attention(saved,'a',{'status':'active','turn_id':'one'})
        update_attention(saved,'a',{'status':'completed','turn_id':'one'})
        self.assertTrue(is_unread(saved,'a'))
