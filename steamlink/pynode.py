#!/usr/bin/env python3

# a Python based Node for SteamLink

import asyncio
import time
import sys

import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

from .mqtt import Mqtt
from .steamlink import (
	SL_OP,
	SL_NodeCfgStruct,
	BasePacket,
)
from .util import phex



class PyNode:
	def __init__(self, nodecfg, conf_mqtt):
		self.nodecfg = nodecfg
		self.slid = self.nodecfg.slid
		self.conf_mqtt = conf_mqtt
		self.mqtt = None
		self.loop = asyncio.get_event_loop()
		logger.info("Start")
		self.on_receive = None

		self.packets_sent = 0
		self.packets_sent = 0
		self.packets_resent = 0
		self.packets_dropped = 0
		self.packets_missed = 0
		self.packets_duplicate = 0
		self.pkt_num = 0

		self.last_packet_tx_ts = 0



	def start(self):
		if self.mqtt is not None:
			logger.error("start called more than once")
			sys.exit(1)
		self.mqtt = Mqtt(self.conf_mqtt)
		logger.debug("startup: create Mqtt")
		self.loop.run_until_complete(self.mqtt.start())

		self.mqtt.set_msg_callback(self.on_data_msg)

		self.signon()

	def on_data_msg(self, client, userdata, msg):
		logger.info("on_data_msg: got %s", msg)
		try:
			pkt = BasePacket(pkt=msg.payload)
		except SteamLinkError as e:
			logger.warning("mqtt: pkt dropped: '%s', steamlink error %s", msg.payload, e)
			return
		except ValueError as e:
			logger.warning("mqtt: pkt dropped: '%s', value error %s", msg.payload, e)
			return

		if pkt.slid != self.slid:
			logger.warning("mqtt: pkt dropped: '%s', not for us", msg.payload)
			return


	def signon(self):
		pkt = BasePacket(self.slid,  SL_OP.val('ON'), payload=self.nodecfg)
		pkt.pkt_num = self.get_pkt_num()
		logger.info("send: %s", pkt)

	def get_pkt_num(self):
		self.pkt_num += 1
		if self.pkt_num == 0:
			self.pkt_num = 1
		return self.pkt_num


	def publish_pkt(self, sl_pkt=None, resend=False, sub="control"):
		if resend:
			logger.debug("resending pkt: %s", sl_pkt)
			self.packets_resent += 1
		else:
			if self.is_waiting_for_AS() and sl_pkt.sl_op != SL_OP.AN:
				logger.error("attempt to send pkt while waiting for AS, ignored: %s", sl_pkt)
				self.packets_dropped += 1
				return
		if len(sl_pkt.pkt) > SL_MAX_MESSAGE_LEN:
			logger.error("publish pkt to long(%s): %s", len(sl_pkt.pkt), sl_pkt)
			return
		self.packets_sent += 1
		if logging.DBG > 1: logger.debug("publish_pkt %s", sl_pkt )
		self.mqtt.publish(self.slid, sl_pkt.pkt, sub=sub)
		self.last_packet_tx_ts = time.time()


	def send(self, packet, slid=1):
		logger.info("sending: '%s' to '%s'", packet, slid)
		sl_op = SL_OP.val('DS')
		payload=packet
		pkt = BasePacket(self.slid, sl_op, 0, payload)
		pkt.pkt_num = self.get_pkt_num()

		logger.info("send: %s", pkt)
		self.publish_pkt(pkt)


	def register_receive_handler(self, on_receive):
		self.on_receive = on_receive


def setup_logging(loglvl):
	logging.DBG = -1
	ch = logging.StreamHandler()
	ch.setLevel(loglvl)
#	formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
	formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
	ch.setFormatter(formatter)
	logger.addHandler(ch)


if __name__ == '__main__':

	mqtt_conf = {
		'clientid': 'pynode_%s' % time.time(),
		'username': 'demo1',
		'password': 'ui712lkm921d',
		'server': 'mqtt.steamlink.net',
		'port': 1883,
		'prefix': 'SteamLink',
		'data': 'data',
		'control': 'control',
	}

	setup_logging(logging.DEBUG)

	logger.info("PyNode Test")
	nodecfg = SL_NodeCfgStruct(slid=401, name="PyNode401", description="Test Py")

	test = PyNode(nodecfg, mqtt_conf)
	test.start()
	
	test.send("Hello World")

	logger.info("done")

