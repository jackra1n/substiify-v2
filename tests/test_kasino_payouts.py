import unittest
from typing import cast

from asyncpg import Record

from extensions.karma import _calculate_payouts


class KasinoPayouts(unittest.TestCase):
	def test_largest_remainder_gets_indivisible_unit(self):
		bets = [
			{"discord_user_id": 11, "amount": 1, "option": 1},
			{"discord_user_id": 12, "amount": 2, "option": 1},
			{"discord_user_id": 13, "amount": 4, "option": 2},
		]
		# The seven-unit pot splits 7/3 and 14/3, so the second winner gets the remainder.
		self.assertEqual(_calculate_payouts(cast(list[Record], bets), 1), {11: 2, 12: 5, 13: 0})

	def test_equal_remainders_favor_lower_user_ids_not_input_order(self):
		bets = [
			{"discord_user_id": 13, "amount": 1, "option": 2},
			{"discord_user_id": 12, "amount": 1, "option": 2},
			{"discord_user_id": 11, "amount": 1, "option": 2},
			{"discord_user_id": 14, "amount": 2, "option": 1},
		]
		self.assertEqual(_calculate_payouts(cast(list[Record], bets), 2), {11: 2, 12: 2, 13: 1, 14: 0})

	def test_large_balances_keep_integer_precision(self):
		stake = 2**60 + 1
		bets = [
			{"discord_user_id": 11, "amount": stake, "option": 1},
			{"discord_user_id": 12, "amount": 1, "option": 1},
			{"discord_user_id": 13, "amount": 1, "option": 2},
		]
		self.assertEqual(_calculate_payouts(cast(list[Record], bets), 1), {11: stake + 1, 12: 1, 13: 0})

	def test_aborted_kasino_refunds_each_stake(self):
		bets = [
			{"discord_user_id": 11, "amount": 3, "option": 1},
			{"discord_user_id": 12, "amount": 7, "option": 2},
		]
		self.assertEqual(_calculate_payouts(cast(list[Record], bets), 3), {11: 3, 12: 7})

	def test_no_winning_bets_pay_nothing(self):
		bets = [
			{"discord_user_id": 11, "amount": 3, "option": 2},
			{"discord_user_id": 12, "amount": 7, "option": 2},
		]
		self.assertEqual(_calculate_payouts(cast(list[Record], bets), 1), {11: 0, 12: 0})
		self.assertEqual(_calculate_payouts([], 1), {})


if __name__ == "__main__":
	unittest.main()
