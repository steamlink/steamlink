#!/usr/bin/env python3

# a Python based Node for SteamLink

import asyncio
import time
import sys

import logging
logger = logging.getLogger()

from .mqtt import Mqtt
from .steamlink import (
	SL_OP,
	SL_NodeCfgStruct,
	BasePacket,
	WaitForAck, 
	SL_MAX_MESSAGE_LEN, 
	SL_ACK_WAIT,
	MAX_RESEND_COUNT,
)
from .util import phex



class PyNode:
	def __init__(self, nodecfg, conf_mqtt, loop):
		self.nodecfg = nodecfg
		self.slid = self.nodecfg.slid
		self.conf_mqtt = conf_mqtt
		self.mqtt = None
		self.loop = loop
		logger.info("Start")
		self.receive_q = None
		self.via = []
		self.packets_sent = 0
		self.packets_sent = 0
		self.packets_resent = 0
		self.packets_dropped = 0
		self.packets_missed = 0
		self.packets_duplicate = 0
		self.pkt_num = 0
		self.wait_for_AN = WaitForAck(SL_ACK_WAIT)
		self.wait_handle = None

		self.last_packet_tx_ts = 0

		self.status = "OK"


	async def start(self):
		if self.mqtt is not None:
			logger.error("start called more than once")
			sys.exit(1)
		self.mqtt = Mqtt(self.conf_mqtt, as_node=True)
		logger.debug("startup: create Mqtt")
		self.mqtt.set_msg_callback(self.on_control_msg)
		await self.mqtt.start()
		self.send_online_to_store()
		logger.debug("startup: done")


	def on_control_msg(self, client, userdata, msg):
		if logging.DBG > 0: logger.info("on_control_msg: got %s", msg.payload)
		try:
			pkt = BasePacket(slnode=self, pkt=msg.payload)
		except SteamLinkError as e:
			logger.warning("mqtt: pkt dropped: '%s', steamlink error %s", msg.payload, e)
			return
		except ValueError as e:
			logger.warning("mqtt: pkt dropped: '%s', value error %s", msg.payload, e)
			return

		if pkt.slid != self.slid:
			if logging.DBG > 1: logger.warning("mqtt: pkt '%s', not for us", msg.payload)
			return


		if pkt.sl_op == SL_OP.AN:		# Ack
			if self.wait_handle is not None:
				self.wait_handle.cancel()
				self.wait_handle = None
			if self.wait_for_AN is not None:
				pkt = self.wait_for_AN.stop_wait()
		elif pkt.sl_op == SL_OP.SC:	
			rc = self.handle_sc(pkt.payload)
			
		elif pkt.sl_op == SL_OP.DN:	
			asyncio.ensure_future(self.receive_q.put(pkt.payload), loop=self.loop)
		elif pkt.sl_op == SL_OP.SS:	
			self.handle_sc(pkt.payload)
		else:
			logger.error("mqtt: for now, not handling %s", pkt)


	def process_ack_timeout(self):
		logger.info("process_ack_timeout")
		self.wait_handle = None
		if self.wait_for_AN.inc_resend_count() > MAX_RESEND_COUNT:
			logger.info("resend limit reached for %s, giving up", self.wait_for_AN)
			self.wait_for_AN.clear_wait()
		else:
			pkt = self.wait_for_AN.restart_wait()
			self.publish_pkt(pkt, resend=True)
	

	def handle_gs(self, payload):
		if logging.DBG > 1: logger.debug("handle gs: %s", payload)
		self.send_status_to_store()


	def handle_sc(self, payload):
		if logging.DBG > 1: logger.debug("handle sc: %s", payload)
		# XXX: actually config  stuff
		self.send_ack_to_store(0)


	def send_data_to_store(self, data):
		sl_pkt = BasePacket(self, sl_op=SL_OP.DS, payload=data)
		self.publish_pkt(sl_pkt)
		self.wait_for_AN.set_wait(sl_pkt)


	def send_ack_to_store(self, code):
		sl_pkt = BasePacket(slnode=self, sl_op=SL_OP.AS, payload=chr(code))
		self.publish_pkt(sl_pkt)


	def send_online_to_store(self):
		sl_pkt = BasePacket(self,  SL_OP.ON, payload=self.nodecfg)
		logger.info("send: %s", sl_pkt)
		if self.publish_pkt(sl_pkt):
			self.wait_for_AN.set_wait(sl_pkt)
			self.wait_handle = self.loop.call_later(SL_ACK_WAIT, self.process_ack_timeout)
			return True
		else:
			return False	


	def send_status_to_store(self):
		sl_pkt = BasePacket(self, sl_op=SL_OP.SS, payload=self.status)
		self.publish_pkt(sl_pkt)


	def set_pkt_number(self, pkt):
		self.pkt_num += 1
		if self.pkt_num == 0:
			self.pkt_num = 1
		return self.pkt_num


	def publish_pkt(self, sl_pkt=None, resend=False, sub="data"):
		if resend:
			if logging.DBG > 1: logger.debug("resending pkt: %s", sl_pkt)
			self.packets_resent += 1
		else:
			if self.wait_for_AN.is_waiting() and sl_pkt.sl_op != SL_OP.AN:
				logger.error("attempt to send pkt while waiting for AN, ignored: %s", sl_pkt)
				self.packets_dropped += 1
				return False
		if len(sl_pkt.pkt) > SL_MAX_MESSAGE_LEN:
			logger.error("publish pkt to long(%s): %s", len(sl_pkt.pkt), sl_pkt)
			return False
		self.packets_sent += 1
		if logging.DBG > 1: logger.debug("publish_pkt %s", sl_pkt )
		self.mqtt.publish(self.slid, sl_pkt.pkt, sub=sub)
		self.last_packet_tx_ts = time.time()
		return True


	# User routins

	def send(self, packet, slid=1):
		logger.info("sending: '%s' to '%s'", packet, slid)
		self.send_data_to_store(packet)


	def register_receive_queue(self, receive_q):
		self.receive_q = receive_q


	def set_status(self, new_status):
		self.status = new_status[:20]
		self.send_status_to_store()


if __name__ == '__main__':

	def setup_logging(loglvl):
		FORMAT='%(name)s - %(levelname)s - %(message)s'
		logging.basicConfig(level=loglvl, format=FORMAT)
	
		logging.DBG = 0
		logging.DBGK = []

	mqtt_conf = {
		'clientid': 'pynode_%s' % time.time(),
		'username': 'demonode0',
		'password': 'ui712lkm921d',
		'server': 'mqtt.steamlink.net',
		'port': 1883,
		'prefix': 'SteamLink',
		'data': 'data',
		'control': 'control',
	}

	async def process_incoming(sl_node, receive_q):
		while True:
			pkt = await receive_q.get()
			print("Got one", pkt)
			sl_node.set_status("HAPPY")
			receive_q.task_done()
		

	async def produce_outgoing(sl_node):
		while True:
			await asyncio.sleep(10)
			sl_node.send("Hello PyWorld")


	async def run(loop):
		logger.info("PyNode Test")
		nodecfg = SL_NodeCfgStruct(slid=401, name="PyNode401", description="Test Py", 
				gps_lat=44.495499, gps_lon=-80.320706)
		
		# create a Queue to receive incoming messages and start the re
		receive_q = asyncio.Queue()

		# create our Node and register the receive queue with it
		sl_node = PyNode(nodecfg, mqtt_conf, loop)
		sl_node.register_receive_queue(receive_q)
		consumer = asyncio.ensure_future(process_incoming(sl_node, receive_q))

		await sl_node.start()	 # go online

		# start outgoing 
		await produce_outgoing(sl_node)

		# exit when the queue closes
		await receive_q.join()

			
	setup_logging(logging.DEBUG)

	loop = asyncio.get_event_loop()
	loop.run_until_complete(run(loop))
	logger.info("done")

