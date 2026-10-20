import unittest

from llm_minimal_probe import NoRedirect, usage_fields


class ProbeValidationTests(unittest.TestCase):
    def test_valid_usage(self):
        body = {'usage': {'prompt_tokens': 10, 'completion_tokens': 2,
                         'total_tokens': 12, 'prompt_tokens_details': {'cached_tokens': 4}}}
        self.assertEqual(usage_fields(body)['cached_tokens'], 4)

    def test_missing_usage_is_not_zero_cost(self):
        with self.assertRaises(ValueError):
            usage_fields({})

    def test_missing_cache_count_is_not_assumed_zero(self):
        with self.assertRaises(ValueError):
            usage_fields({'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}})

    def test_invalid_counts(self):
        for cached, total in ((11, 12), (-1, 12), (0, 13), (True, 12)):
            with self.subTest(cached=cached, total=total), self.assertRaises(ValueError):
                usage_fields({'usage': {'prompt_tokens': 10, 'completion_tokens': 2,
                    'total_tokens': total, 'prompt_tokens_details': {'cached_tokens': cached}}})

    def test_no_redirect_with_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))


if __name__ == '__main__':
    unittest.main()
