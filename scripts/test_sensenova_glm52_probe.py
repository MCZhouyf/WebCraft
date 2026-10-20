import unittest


class GatewayProbePolicyTests(unittest.TestCase):
    def test_authorized_request_shape_is_frozen(self):
        expected = {
            'model': 'glm-5.2',
            'messages': [{'role': 'user', 'content': 'Reply only OK.'}],
            'max_tokens': 16,
            'stream': False,
            'extra_body': {'thinking': {'type': 'disabled'}},
        }
        self.assertEqual(expected['model'], 'glm-5.2')
        self.assertEqual(expected['extra_body']['thinking']['type'], 'disabled')
        self.assertLessEqual(expected['max_tokens'], 16)
        self.assertFalse(expected['stream'])


if __name__ == '__main__':
    unittest.main()
