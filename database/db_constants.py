USER_INSERT_QUERY = """INSERT INTO discord_user
                       (discord_user_id, username, avatar)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (discord_user_id) DO UPDATE
                       SET
                       username = EXCLUDED.username,
                       avatar = EXCLUDED.avatar
                    """

SERVER_INSERT_QUERY = """INSERT INTO discord_server
                         (discord_server_id, server_name)
                         VALUES ($1, $2)
                         ON CONFLICT (discord_server_id) DO UPDATE
                         SET
                         server_name = EXCLUDED.server_name
                      """

CHANNEL_INSERT_QUERY = """INSERT INTO discord_channel
                          (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id)
                          VALUES ($1, $2, $3, $4)
                          ON CONFLICT (discord_channel_id) DO UPDATE
                          SET
                          channel_name = EXCLUDED.channel_name,
                          parent_discord_channel_id = EXCLUDED.parent_discord_channel_id
                       """
