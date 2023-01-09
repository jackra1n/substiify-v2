

CREATE DATABASE substiify;

CREATE TABLE IF NOT EXISTS discord_server (
  discord_server_id BIGINT PRIMARY KEY,
  server_name STRING,
  music_cleanup BOOLEAN
)

CREATE TABLE IF NOT EXISTS discord_channel (
  discord_channel_id BIGINT PRIMARY KEY,
  channel_name STRING,
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id) ON DELETE CASCADE,
  parent_discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  upvote BOOLEAN DEFAULT 0
)

CREATE TABLE IF NOT EXISTS discord_user (
  discord_user_id BIGINT PRIMARY KEY,
  username STRING,
  discriminator STRING,
  avatar STRING,
  is_bot BOOLEAN
)

CREATE TABLE IF NOT EXISTS command_history (
  id BIGINT PRIMARY KEY,
  command_name STRING,
  date TIMESTAMP,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT
)

CREATE TABLE IF NOT EXISTS giveaway (
  id BIGINT PRIMARY KEY,
  start_date TIMESTAMP,
  end_date TIMESTAMP NOT NULL,
  prize STRING,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT NOT NULL
)

CREATE TABLE IF NOT EXISTS karma (
  id BIGINT PRIMARY KEY,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  amount BIGINT
)

CREATE TABLE IF NOT EXISTS post (
  discord_message_id BIGINT PRIMARY KEY,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  created_at TIMESTAMP NOT NULL,
  upvotes BIGINT DEFAULT 0,
  downvotes BIGINT DEFAULT 0
)

CREATE TABLE IF NOT EXISTS karma_emote (
  discord_emote_id BIGINT PRIMARY KEY,
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  increase_karma BOOLEAN
)

CREATE TABLE IF NOT EXISTS kasino (
  id BIGINT PRIMARY KEY,
  question STRING NOT NULL,
  option1 STRING NOT NULL,
  option2 STRING NOT NULL,
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT NOT NULL,
  locked BOOLEAN DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)

CREATE TABLE IF NOT EXISTS kasino_bet (
  id BIGINT PRIMARY KEY,
  kasino_id BIGINT REFERENCES kasino(id),
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  amount BIGINT,
  option BIGINT
)