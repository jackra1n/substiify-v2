CREATE TABLE command_history_new (
	id INTEGER NOT NULL, 
	command VARCHAR, 
	date DATETIME, 
	discord_user_id INTEGER, 
	discord_server_id INTEGER, 
	discord_channel_id INTEGER, 
	discord_message_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(discord_user_id) REFERENCES discord_user (discord_user_id), 
	FOREIGN KEY(discord_server_id) REFERENCES discord_server (discord_server_id), 
	FOREIGN KEY(discord_channel_id) REFERENCES discord_channel (discord_channel_id)
)

INSERT INTO command_history_new
(
    id, 
    command, 
    date, 
    discord_user_id, 
    discord_server_id, 
    discord_channel_id, 
    discord_message_id
)
SELECT 
    id, 
    command, 
    date, 
    discord_user_id, 
    discord_server_id, 
    discord_channel_id, 
    discord_message_id
FROM command_history;

DROP TABLE command_history;
ALTER TABLE command_history_new RENAME TO command_history;


CREATE TABLE karma_new (
	id INTEGER NOT NULL, 
	discord_user_id INTEGER, 
	discord_server_id INTEGER, 
	amount INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(discord_user_id) REFERENCES discord_user (discord_user_id), 
	FOREIGN KEY(discord_server_id) REFERENCES discord_server (discord_server_id)
)

INSERT INTO karma_new
(
    id, 
    discord_user_id, 
    discord_server_id, 
    amount
)
SELECT 
    id, 
    discord_user_id, 
    discord_server_id, 
    amount
FROM karma;

DROP TABLE karma;
ALTER TABLE karma_new RENAME TO karma;



CREATE TABLE karma_emote (
	discord_emote_id INTEGER NOT NULL, 
	discord_server_id INTEGER, 
	action INTEGER, 
	PRIMARY KEY (discord_emote_id), 
	FOREIGN KEY(discord_server_id) REFERENCES discord_server (discord_server_id)
)

INSERT INTO karma_emote
(
    discord_emote_id, 
    discord_server_id, 
    action
)
SELECT 
    discord_emote_id, 
    discord_server_id, 
    action
FROM karma_emote;

DROP TABLE karma_emote;
ALTER TABLE karma_emote_new RENAME TO karma_emote;


INSERT INTO post
(
    discord_message_id,
    discord_user_id,
    discord_server_id,
    discord_channel_id,
    created_at,
    upvotes,
    downvotes,
)
SELECT
    user_id,
    server_id,
    channel_id,
    message_id,
    created_at,
    upvotes,
    downvotes
FROM posts;


DROP TABLE posts;
DROP TABLE active_giveaways;
