import logging
from datetime import datetime

import nextcord
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from utils import store

logger = logging.getLogger(__name__)

engine = create_engine(f'sqlite:///{store.DB_PATH}')
session = sessionmaker(bind=engine)()

Base = declarative_base()

class command_history(Base):
    __tablename__ = 'command_history'

    id = Column(Integer, primary_key=True)
    command = Column(String)
    date = Column(DateTime)
    user_id = Column(Integer)
    server_id = Column(Integer)
    channel_id = Column(Integer)
    message_id = Column(Integer)

    def __init__(self, command, message):
        self.command = command.qualified_name
        self.date = datetime.now()
        self.user_id = message.author.id
        self.message_id = message.id
        self.server_id = None
        self.channel_id = None
        if isinstance(message.channel, nextcord.channel.TextChannel):
            self.server_id = message.guild.id
            self.channel_id = message.channel.id

class active_giveaways(Base):
    __tablename__ = 'active_giveaways'

    id = Column(Integer, primary_key=True)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    prize = Column(String)
    creator_user_id = Column(Integer)
    server_id = Column(Integer)
    channel_id = Column(Integer)
    message_id = Column(Integer)

    def __init__(self, creator, end_date, prize, giveaway_message):
        self.start_date = datetime.now()
        self.end_date = end_date
        self.prize = prize
        self.creator_user_id = creator.id
        self.message_id = giveaway_message.id
        self.server_id = giveaway_message.guild.id
        self.channel_id = giveaway_message.channel.id

class vote_channels(Base):
    __tablename__ = 'vote_channels'

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer)
    channel_id = Column(Integer)

    def __init__(self, server_id, channel_id):
        self.server_id = server_id
        self.channel_id = channel_id

class karma(Base):
    __tablename__ = 'karma'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    guild_id = Column(Integer)
    amount = Column(Integer)

    def __init__(self, user_id, guild_id, amount):
        self.user_id = user_id
        self.guild_id = guild_id
        self.amount = amount

class post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    server_id = Column(Integer)
    channel_id = Column(Integer)
    message_id = Column(Integer)
    created_at = Column(DateTime)
    upvotes = Column(Integer)
    downvotes = Column(Integer)

    def __init__(self, message, upvotes, downvotes):
        self.user_id = message.author.id
        self.server_id = message.guild.id
        self.channel_id = message.channel.id
        self.message_id = message.id
        self.created_at = message.created_at
        self.upvotes = upvotes
        self.downvotes = downvotes

class karma_emote(Base):
    __tablename__ = 'karma_emote'

    id = Column(Integer, primary_key=True)
    guild_id = Column(Integer)
    emote_id = Column(Integer)
    action = Column(Integer)

    def __init__(self, guild_id, emote_id, action):
        self.guild_id = guild_id
        self.emote_id = emote_id
        self.action = action

class user_rank(Base):
    __tablename__ = 'user_rank'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    server_id = Column(Integer)
    vc_rank_points = Column(Integer)
    message_rank_points = Column(Integer)

# Creates database tables if the don't exist
def create_database():
    Base.metadata.create_all(engine)
