import unittest

from extensions.giveaways import MAX_DURATION, parse_duration


class TestParseDuration(unittest.TestCase):
	def test_valid_durations(self):
		self.assertEqual(parse_duration("10m"), 600)
		self.assertEqual(parse_duration("2h"), 7200)
		self.assertEqual(parse_duration("1D"), 86400)

	def test_invalid_durations_rejected(self):
		for raw in ["", "m", "10", "-5m", "0m", "1.5h", "10s", "1h30m", "366d", "9" * 400 + "d"]:
			with self.subTest(raw=raw):
				self.assertIsNone(parse_duration(raw))

	def test_max_duration_accepted(self):
		self.assertEqual(parse_duration("365d"), MAX_DURATION)
