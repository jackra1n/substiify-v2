import logging
import sqlite3
from datetime import datetime

import discord
from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        create_engine)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from utils import store

logger = logging.getLogger(__name__)

engine = create_engine(f'sqlite:///{store.DB_PATH}')
session = sessionmaker(bind=engine)()

Base = declarative_base()

class discord_server(Base):
    __tablename__ = 'discord_server'

    discord_server_id = Column(Integer, primary_key=True)
    server_name = Column(String)
    music_cleanup = Column(Boolean, default=False)

    def __init__(self, server: discord.Guild):
        self.discord_server_id = server.id
        self.server_name = server.name


class discord_channel(Base):
    __tablename__ = 'discord_channel'

    discord_channel_id = Column(Integer, primary_key=True)
    channel_name = Column(String)
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    parent_discord_channel_id = Column(Integer, ForeignKey('discord_channel.discord_channel_id'))
    upvote = Column(Boolean, default=False)

    def __init__(self, channel: discord.TextChannel):
        self.discord_channel_id = channel.id
        self.channel_name = channel.name
        self.discord_server_id = channel.guild.id
        self.parent_discord_channel_id = channel.category_id if channel.category else None


class discord_user(Base):
    __tablename__ = 'discord_user'

    discord_user_id = Column(Integer, primary_key=True)
    username = Column(String)
    discriminator = Column(String)
    avatar = Column(String)
    is_bot = Column(Boolean)
    nickname = Column(String)

    def __init__(self, user: discord.User):
        self.discord_user_id = user.id
        self.username = user.name
        self.discriminator = user.discriminator
        self.avatar = user.avatar_url if user.avatar else None
        self.is_bot = user.bot
        self.nickname = user.display_name


class command_history(Base):
    __tablename__ = 'command_history'

    id = Column(Integer, primary_key=True)
    command = Column(String)
    date = Column(DateTime, default=datetime.now())
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    discord_channel_id = Column(Integer, ForeignKey('discord_channel.discord_channel_id'))
    discord_message_id = Column(Integer)

    def __init__(self, ctx):
        self.command = ctx.command.root_parent.qualified_name if ctx.command.root_parent else ctx.command.qualified_name
        self.discord_user_id = ctx.author.id
        self.discord_message_id = ctx.message.id
        self.discord_server_id = ctx.message.guild.id if ctx.guild else None
        self.discord_channel_id = ctx.channel.id


class giveaway(Base):
    __tablename__ = 'giveaway'

    id = Column(Integer, primary_key=True)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    prize = Column(String)
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    discord_channel_id = Column(Integer, ForeignKey('discord_channel.discord_channel_id'))
    discord_message_id = Column(Integer)

    def __init__(self, creator, end_date, prize, giveaway_message):
        self.start_date = datetime.now()
        self.end_date = end_date
        self.prize = prize
        self.discord_user_id = creator.id
        self.discord_message_id = giveaway_message.id
        self.discord_server_id = giveaway_message.guild.id
        self.discord_channel_id = giveaway_message.channel.id

class karma(Base):
    __tablename__ = 'karma'

    id = Column(Integer, primary_key=True)
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    amount = Column(Integer, default=0)

    def __init__(self, user_id, server_id, amount):
        self.discord_user_id = user_id
        self.discord_server_id = server_id
        self.amount = amount

class post(Base):
    __tablename__ = 'post'

    discord_message_id = Column(Integer, primary_key=True)
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    discord_channel_id = Column(Integer, ForeignKey('discord_channel.discord_channel_id'))
    created_at = Column(DateTime)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)

    def __init__(self, message, upvotes, downvotes):
        self.discord_user_id = message.author.id
        self.discord_server_id = message.guild.id
        self.discord_channel_id = message.channel.id
        self.discord_message_id = message.id
        self.created_at = message.created_at
        self.upvotes = upvotes
        self.downvotes = downvotes

class karma_emote(Base):
    __tablename__ = 'karma_emote'

    discord_emote_id = Column(Integer, primary_key=True)
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    action = Column(Integer)

    def __init__(self, emote, action):
        self.discord_server_id = emote.guild_id
        self.discord_emote_id = emote.id
        self.action = action

class user_rank(Base):
    __tablename__ = 'user_rank'

    id = Column(Integer, primary_key=True)
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    vc_rank_points = Column(Integer)
    message_rank_points = Column(Integer)

    def __init__(self, user, vc_rank_points, message_rank_points):
        self.discord_user_id = user.id
        self.discord_server_id = user.guild.id
        self.vc_rank_points = vc_rank_points
        self.message_rank_points = message_rank_points

class kasino(Base):
    __tablename__ = 'kasino'

    id = Column(Integer, primary_key=True)
    question = Column(String)
    option_1 = Column(String)
    option_2 = Column(String)
    discord_server_id = Column(Integer, ForeignKey('discord_server.discord_server_id'))
    discord_channel_id = Column(Integer, ForeignKey('discord_channel.discord_channel_id'))
    discord_message_id = Column(Integer)
    locked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now())

    def __init__(self, question, option_1, option_2, message):
        self.question = question
        self.option_1 = option_1
        self.option_2 = option_2
        self.discord_server_id = message.guild.id
        self.discord_channel_id = message.channel.id
        self.discord_message_id = message.id

class kasino_bet(Base):
    __tablename__ = 'kasino_bet'

    id = Column(Integer, primary_key=True)
    kasino_id = Column(Integer, ForeignKey('kasino.id'))
    discord_user_id = Column(Integer, ForeignKey('discord_user.discord_user_id'))
    amount = Column(Integer)
    option = Column(Integer)

    def __init__(self, kasino_id, user_id, amount, option):
        self.kasino_id = kasino_id
        self.discord_user_id = user_id
        self.amount = amount
        self.option = option

def get_discord_server(server: discord.Guild):
    db_server = session.query(discord_server).filter_by(discord_server_id=server.id).first()
    if db_server is None:
        db_server = discord_server(server)
        session.add(db_server)
        session.commit()
    return db_server

def get_discord_channel(channel: discord.TextChannel):
    db_channel = session.query(discord_channel).filter_by(discord_channel_id=channel.id).first()
    if db_channel is None:
        db_channel = discord_channel(channel)
        session.add(db_channel)
        session.commit()
    return db_channel


def convert_db(version):
    connection = sqlite3.connect(store.DB_PATH)

    cursor = connection.cursor()

    sql_file = open(f'{store.RESOURCES_PATH}/db_conversion/ConvertDatabaseVersion{version}.sql')
    sql_as_string = sql_file.read()
    cursor.executescript(sql_as_string)
    connection.commit()
    connection.close()

# Creates database tables if the don't exist
def create_database():
    Base.metadata.create_all(engine)
