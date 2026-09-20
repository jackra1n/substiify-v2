-- Owner.last filters by server and orders by newest commands before LIMIT.
CREATE INDEX command_history_server_date ON command_history(discord_server_id, date DESC);
