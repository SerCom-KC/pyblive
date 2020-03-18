import json
import logging
import struct
import time
import zlib
from threading import Thread

from websocket import ABNF, WebSocketApp

WS_OP_HEARTBEAT = 2
WS_OP_HEARTBEAT_REPLY = 3
WS_OP_MESSAGE = 5
WS_OP_USER_AUTHENTICATION = 7
WS_OP_CONNECT_SUCCESS = 8
WS_PACKAGE_HEADER_TOTAL_LENGTH = 16
WS_PACKAGE_OFFSET = 0
WS_HEADER_OFFSET = 4
WS_VERSION_OFFSET = 6
WS_OPERATION_OFFSET = 8
WS_SEQUENCE_OFFSET = 12
WS_BODY_PROTOCOL_VERSION_NORMAL = 0
WS_BODY_PROTOCOL_VERSION_DEFLATE = 2
WS_HEADER_DEFAULT_VERSION = 1
WS_HEADER_DEFAULT_OPERATION = 1
WS_HEADER_DEFAULT_SEQUENCE = 1
WS_AUTH_OK = 0
WS_AUTH_TOKEN_ERROR = -101

# packet_length, header_length, protocol_version, operation, sequence_id
WS_PACKAGE_HEADER_STRUCT = "!IHHII"


class Client(WebSocketApp):
    def __init__(self, room_id, config, secure=True, uid=0, platform="web", client_ver="1.10.3", proto_ver=WS_BODY_PROTOCOL_VERSION_DEFLATE, message_callback=None, logger=logging.getLogger(__name__)):
        self.header_struct = struct.Struct(WS_PACKAGE_HEADER_STRUCT)
        if self.header_struct.size != WS_PACKAGE_HEADER_TOTAL_LENGTH:
            raise ValueError("Length of header struct does not match")
        self.room_id = room_id
        self.config = config
        self.uid = uid
        self.key = self.config["token"]
        self.proto_ver = proto_ver
        self.platform = platform
        self.client_ver = client_ver
        # TODO auto switch server
        self.server = self.config["host_server_list"][0]
        self.host = self.server["host"]
        self.port = self.server["wss_port"] if secure else self.server["ws_port"]
        super().__init__("ws%s://%s:%s/sub" % ("s" if secure else "", self.host, self.port),
                         on_message=self.__message_handler, on_open=self.__login, on_close=self.stop)
        self.logger = logger
        self.heartbeat_stop = True
        self.last_heartbeat_timestamp = -1
        self.message_callback = message_callback
        self.logger.info("Initiated")
        self.login_complete = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def send(self, data, protocol_version=None, operation=WS_HEADER_DEFAULT_OPERATION, sequence_id=WS_HEADER_DEFAULT_SEQUENCE, no_compress=False):
        if protocol_version is None:
            protocol_version = self.proto_ver
        if isinstance(data, dict):
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
        if protocol_version == WS_BODY_PROTOCOL_VERSION_DEFLATE and not no_compress:
            header = self.header_struct.pack(WS_PACKAGE_HEADER_TOTAL_LENGTH + len(
                body), WS_PACKAGE_HEADER_TOTAL_LENGTH, WS_BODY_PROTOCOL_VERSION_NORMAL, operation, sequence_id)
            self.logger.debug("Compressing packet")
            return self.send(zlib.compress(header + body), protocol_version, operation, sequence_id, no_compress=True)
        else:
            header = self.header_struct.pack(WS_PACKAGE_HEADER_TOTAL_LENGTH + len(
                body), WS_PACKAGE_HEADER_TOTAL_LENGTH, protocol_version, operation, sequence_id)
        super().send(header + body, opcode=ABNF.OPCODE_BINARY)

    def __login(self, ws):
        data = {
            "uid": self.uid,
            "roomid": self.room_id,
            "protover": self.proto_ver,
            "platform": self.platform,
            "clientver": self.client_ver,
            "key": self.key
        }
        self.logger.info("Authenticating")
        self.send(data, protocol_version=WS_BODY_PROTOCOL_VERSION_NORMAL,
                  operation=WS_OP_USER_AUTHENTICATION)

    def __message_handler(self, ws, packet):
        try:
            self.logger.debug("Received packet length: %s" % (len(packet)))
            packet_length, header_length, protocol_version, operation, sequence_id = self.header_struct.unpack_from(
                packet)
            self.logger.debug(
                "Headers: packet_length=%s, header_length=%s, protocol_version=%s, operation=%s, sequence_id=%s" % (packet_length, header_length, protocol_version, operation, sequence_id))
            body = packet[header_length:packet_length]
            if protocol_version == WS_BODY_PROTOCOL_VERSION_DEFLATE:
                self.logger.debug("Decompressing packet")
                body = zlib.decompress(body)
                self.__message_handler(ws, body)
            else:
                try:
                    body = json.loads(body)
                except json.decoder.JSONDecodeError:
                    pass
                if operation == WS_OP_CONNECT_SUCCESS and body["code"] == 0:
                    self.logger.info("Websocket connection successful")
                    self.login_complete = True
                elif operation == WS_OP_HEARTBEAT_REPLY:
                    body = int.from_bytes(body, byteorder="big", signed=False)
                    self.logger.info(
                        "Received heartbeat reply packet, online count is %s" % (body))
                elif operation == WS_OP_MESSAGE:
                    self.logger.info("Received message: %s" % (body))
                else:
                    raise RuntimeError(
                        "Unknown operation received", operation, body)
                if self.message_callback is not None:
                    Thread(target=self.message_callback, args=(
                        self, operation, sequence_id, body,)).start()
            if len(packet) > packet_length:
                self.logger.debug("Extra packet detected")
                self.__message_handler(ws, packet[packet_length:])
        except Exception as e:
            self.logger.error("Failed to parse packet: %s" % (e))

    def heartbeat(self):
        self.logger.info("Sending heartbeat packet")
        self.send("[object Object]",
                  WS_BODY_PROTOCOL_VERSION_NORMAL, WS_OP_HEARTBEAT)

    def __heart(self):
        self.logger.info("Heartbeat thread started")
        while not self.heartbeat_stop and self.keep_running:
            if int(time.time()) - self.last_heartbeat_timestamp >= 30:
                self.last_heartbeat_timestamp = int(time.time())
                self.heartbeat()
        self.logger.info("Heartbeat thread stopped")

    def stop(self):
        self.logger.info("Disconnecting")
        self.keep_running = False
        self.heartbeat_stop = True

    def start(self):
        Thread(target=super().run_forever).start()
        while not self.login_complete and self.keep_running:
            self.logger.debug("Waiting for authentication complete")
            time.sleep(1)
        self.heartbeat_stop = False
        Thread(target=self.__heart).start()

    def idle(self):
        try:
            while self.keep_running:
                continue
        except KeyboardInterrupt as e:
            self.stop()
            raise e

    def _callback(self, callback, *args):
        if callback == self.stop:
            self.stop()
        elif callback:
            callback(self, *args)
