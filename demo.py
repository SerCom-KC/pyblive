import logging
import sys
import time

import requests

from blive import *

ROOM_ID = 37702


def message_handler(client, operation, sequence_id, body):
    if operation == WS_OP_CONNECT_SUCCESS and body["code"] == 0:
        print("Successfully connected to room %s" % (client.room_id))
    elif operation == WS_OP_HEARTBEAT_REPLY:
        print("Current online count is %s" % (body))
    elif operation == WS_OP_MESSAGE and body["cmd"] == "DANMU_MSG":
        print("New danmaku from %s [%s]: %s" % (
            body["info"][2][1], body["info"][2][0], body["info"][1]))


#logging.basicConfig(stream=sys.stderr, level=logging.DEBUG, format="[%(asctime)s][%(levelname)s][%(module)s.%(funcName)s] %(message)s")
config = requests.get("https://api.live.bilibili.com/room/v1/Danmu/getConf",
                      params={"room_id": ROOM_ID}).json()["data"]
client = Client(ROOM_ID, config,
                message_callback=message_handler, secure=False)
client.start()
client.idle()
