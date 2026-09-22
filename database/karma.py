from collections.abc import Iterable

import asyncpg


async def lock_karma_rows(conn: asyncpg.Connection, guild_id: int, user_ids: Iterable[int]) -> None:
	# Always lock in user-ID order, including inserts for participants with no balance row.
	for user_id in sorted(set(user_ids)):
		await conn.execute(
			"""INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, 0)
			ON CONFLICT (discord_user_id, discord_server_id) DO NOTHING""",
			user_id,
			guild_id,
		)
		await conn.fetchval(
			"SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2 FOR UPDATE",
			user_id,
			guild_id,
		)
