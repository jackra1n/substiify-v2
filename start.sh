#!/bin/bash

# start bot in a new screen

screen -dmS nextcord-bot python3 bot.py
screen -t nextcord-bot -X multiuser on
