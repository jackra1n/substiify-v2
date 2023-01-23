
CREATE TABLE IF NOT EXISTS discord_server (
  discord_server_id BIGINT PRIMARY KEY,
  server_name VARCHAR(255),
  music_cleanup BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS discord_channel (
  discord_channel_id BIGINT PRIMARY KEY,
  channel_name VARCHAR(255),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id) ON DELETE CASCADE,
  parent_discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  upvote BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS discord_user (
  discord_user_id BIGINT PRIMARY KEY,
  username VARCHAR(255),
  discriminator VARCHAR(10),
  avatar VARCHAR(255),
  is_bot BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS command_history (
  id SERIAL PRIMARY KEY,
  command_name VARCHAR(255),
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT,
  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS giveaway (
  id SERIAL PRIMARY KEY,
  start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  end_date TIMESTAMP NOT NULL,
  prize VARCHAR(255),
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS karma (
  id SERIAL PRIMARY KEY,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  amount BIGINT
);

CREATE TABLE IF NOT EXISTS post (
  discord_message_id BIGINT PRIMARY KEY,
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  created_at TIMESTAMP NOT NULL,
  upvotes BIGINT DEFAULT 0,
  downvotes BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS karma_emote (
  id SERIAL PRIMARY KEY,
  discord_emote_id BIGINT,
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  increase_karma BOOLEAN
);

CREATE TABLE IF NOT EXISTS kasino (
  id SERIAL PRIMARY KEY,
  question VARCHAR(255) NOT NULL,
  option1 VARCHAR(255) NOT NULL,
  option2 VARCHAR(255) NOT NULL,
  discord_server_id BIGINT REFERENCES discord_server(discord_server_id),
  discord_channel_id BIGINT REFERENCES discord_channel(discord_channel_id),
  discord_message_id BIGINT NOT NULL,
  locked BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kasino_bet (
  id SERIAL PRIMARY KEY,
  kasino_id BIGINT REFERENCES kasino(id),
  discord_user_id BIGINT REFERENCES discord_user(discord_user_id),
  amount BIGINT,
  option BIGINT
);