#!/bin/bash

# start bot in a new screen

screen -dmS substiify python3 bot.py
screen -t substiify -X multiuser on
