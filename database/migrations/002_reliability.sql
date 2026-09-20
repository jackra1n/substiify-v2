-- Existing naive values were written as UTC by the bot. Preserve their instants.
ALTER TABLE command_history ALTER COLUMN date TYPE TIMESTAMPTZ USING date AT TIME ZONE 'UTC';
ALTER TABLE command_error ALTER COLUMN date TYPE TIMESTAMPTZ USING date AT TIME ZONE 'UTC';
ALTER TABLE giveaway
    ALTER COLUMN start_date TYPE TIMESTAMPTZ USING start_date AT TIME ZONE 'UTC',
    ALTER COLUMN end_date TYPE TIMESTAMPTZ USING end_date AT TIME ZONE 'UTC';
ALTER TABLE post ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE kasino ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE feedback ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE free_game_history
    ALTER COLUMN start_date TYPE TIMESTAMPTZ USING start_date AT TIME ZONE 'UTC',
    ALTER COLUMN end_date TYPE TIMESTAMPTZ USING end_date AT TIME ZONE 'UTC',
    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';

ALTER TABLE kasino
    ADD COLUMN settled_at TIMESTAMPTZ,
    ADD COLUMN winning_option SMALLINT CHECK (winning_option IN (1, 2, 3)),
    ADD CONSTRAINT kasino_settlement_complete CHECK ((settled_at IS NULL) = (winning_option IS NULL));
ALTER TABLE kasino_bet ADD COLUMN payout BIGINT CHECK (payout >= 0);
-- Enforce new writes without rewriting or discarding historical invalid bets.
ALTER TABLE kasino_bet ADD CONSTRAINT kasino_bet_valid_amount CHECK (amount IS NOT NULL AND amount > 0) NOT VALID;
ALTER TABLE kasino_bet ADD CONSTRAINT kasino_bet_valid_option CHECK (option IS NOT NULL AND option IN (1, 2)) NOT VALID;

ALTER TABLE feedback ALTER COLUMN discord_message_id DROP NOT NULL;
CREATE UNIQUE INDEX feedback_message_id ON feedback(discord_message_id) WHERE discord_message_id IS NOT NULL;

CREATE TABLE free_game_delivery (
    store_name TEXT NOT NULL,
    store_link TEXT NOT NULL,
    promotion_key TEXT NOT NULL,
    discord_channel_id BIGINT NOT NULL REFERENCES discord_channel(discord_channel_id) ON DELETE CASCADE,
    delivered_at TIMESTAMPTZ,
    claimed_until TIMESTAMPTZ,
    claim_token UUID,
    PRIMARY KEY (store_name, store_link, promotion_key, discord_channel_id)
);
-- The old global history cannot prove which channels received a promotion.
-- Preserve its existing suppression window for configured destinations at cutover.
INSERT INTO free_game_delivery(store_name, store_link, promotion_key, discord_channel_id, delivered_at)
SELECT history.store_name, history.store_link, 'legacy', channels.discord_channel_id, MAX(history.created_at)
FROM free_game_history AS history
JOIN store_options AS options ON options.store_name = history.store_name
JOIN free_games_channel AS channels ON channels.id = options.free_games_channel_id
WHERE channels.discord_channel_id IS NOT NULL
GROUP BY history.store_name, history.store_link, channels.discord_channel_id;
