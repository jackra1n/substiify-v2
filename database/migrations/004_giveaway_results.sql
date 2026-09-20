-- NULL winners_count preserves existing giveaways whose count lives in the source embed.
ALTER TABLE giveaway
    ADD COLUMN winners_count SMALLINT CHECK (winners_count BETWEEN 1 AND 10),
    ADD COLUMN result_version INTEGER NOT NULL DEFAULT 0 CHECK (result_version >= 0),
    ADD COLUMN cancelled_at TIMESTAMPTZ,
    ADD COLUMN unavailable_at TIMESTAMPTZ,
    ADD COLUMN unavailable_reason TEXT,
    ADD CONSTRAINT giveaway_cancel_unselected CHECK (cancelled_at IS NULL OR result_version = 0),
    ADD CONSTRAINT giveaway_unavailable_reason CHECK ((unavailable_at IS NULL) = (unavailable_reason IS NULL));

-- Message IDs are globally unique; historical rerolls can register a source only once.
CREATE UNIQUE INDEX giveaway_source_message ON giveaway(discord_message_id);

-- Each explicit reroll appends a result; automatic retries never change an existing draw.
-- An empty array is a selected, durable no-entrant result, not an unselected giveaway.
CREATE TABLE giveaway_result (
    giveaway_id INTEGER NOT NULL REFERENCES giveaway(id),
    version INTEGER NOT NULL CHECK (version > 0),
    winners_count SMALLINT NOT NULL CHECK (winners_count BETWEEN 1 AND 10),
    winner_ids BIGINT[] NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    announcement_message_id BIGINT CHECK (announcement_message_id > 0),
    message_edited_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    delivery_token UUID,
    delivery_until TIMESTAMPTZ,
    PRIMARY KEY (giveaway_id, version),
    CONSTRAINT giveaway_result_winners CHECK (
        cardinality(winner_ids) <= winners_count
        AND (cardinality(winner_ids) = 0 OR array_ndims(winner_ids) = 1)
        AND array_position(winner_ids, NULL) IS NULL
        AND 0 < ALL(winner_ids)
    ),
    CONSTRAINT giveaway_result_lease CHECK ((delivery_token IS NULL) = (delivery_until IS NULL)),
    CONSTRAINT giveaway_result_edit_announced CHECK (
        message_edited_at IS NULL OR announcement_message_id IS NOT NULL
    ),
    CONSTRAINT giveaway_result_complete CHECK (
        completed_at IS NULL OR (
            announcement_message_id IS NOT NULL AND message_edited_at IS NOT NULL AND delivery_token IS NULL
        )
    )
);

CREATE INDEX giveaway_pending_end ON giveaway(end_date)
    WHERE cancelled_at IS NULL AND unavailable_at IS NULL;
