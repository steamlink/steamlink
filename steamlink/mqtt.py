import asyncio
import json
import logging
import sys

import aiomqtt
from hbmqtt.broker import Broker

from . import (DBG, DBGK)


logger = logging.getLogger(__name__)


#
# Mqtt
#
class Mqtt:
	def __init__(self, conf, loop=None, as_node=False):
		self.conf = conf
		self.name = "mqtt"
		if loop is None:
			self.loop = asyncio.get_event_loop()
		else:
			self.loop = loop
		self.as_node = as_node

		self.topic_prefix = conf['prefix']
		self.topic_control = conf['control']
		self.topic_data = conf['data']
		self.public_topic_control = conf.get('public_control', None)
		self.public_topic_data = conf.get('public_data', None)
		self.server = conf['server']
		self.port = conf['port']
		self.clientid = conf['clientid']
		self.username = conf['username']
		self.password = conf['password']
		self.ssl_certificate = conf.get('ssl_certificate', None)

		self.control_topic_x = "%s/%%s/%s" % (self.topic_prefix, self.topic_control)
		self.control_topic = "%s/+/%s" % (self.topic_prefix, self.topic_control)
		self.data_topic_x = "%s/%%s/%s" % (self.topic_prefix, self.topic_data)
		self.data_topic = "%s/+/%s" % (self.topic_prefix, self.topic_data)

		self.connected = asyncio.Event(loop=loop)
		self.subscribed = asyncio.Event(loop=loop)
		self.disconnected = asyncio.Event(loop=loop)
		self.mq = self.set_mq()
		if self.as_node:
			self.subscription_list = [self.control_topic]
		else:
			self.subscription_list = [self.data_topic]
		if self.public_topic_control not in ['', None]:
			self.public_control_topic = self.public_topic_control % "+"
			self.subscription_list.append(self.public_control_topic)
		else:
			self.public_control_topic = None
		self.running = True


	def set_mq(self):

		mq = aiomqtt.Client(client_id=self.clientid, loop=self.loop)
		mq.loop_start()
		if DBG > 1: mq.enable_logger(logger)
		if self.ssl_certificate:
			logger.debug("%s: using cert %s", self.name, self.ssl_certificate)
			try:
				mq.tls_set(self.ssl_certificate)
			except FileNotFoundError as e:
				logger.error("Mqtt: tls_set certificate %s: %s", self.ssl_certificate, e)
				sys.exit(1)
			mq.tls_insecure_set(False)
		if self.username and self.password:
			mq.username_pw_set(self.username, self.password)
		mq.on_connect = self.on_connect
		mq.on_subscribe = self.on_subscribe
		mq.on_message = self.on_message
		mq.on_disconnect = self.on_disconnect
		return mq


	def get_public_control_topic(self):
		return self.public_topic_control


	def set_msg_callback(self, callback):
		if self.as_node:
			logger.debug("set_msg_callback on %s", self.control_topic)
			self.mq.message_callback_add(self.control_topic, callback)
		else:
			logger.debug("set_msg_callback on %s", self.data_topic)
			self.mq.message_callback_add(self.data_topic, callback)


	def set_public_control_callback(self, callback):
		logger.debug("set_public_control_callback on %s", self.public_control_topic)
		self.mq.message_callback_add(self.public_control_topic, callback)


	async def start(self):
		logger.info("%s connecting to %s:%s", self.name, self.server, self.port)

		while True:
			try:
				await self.mq.connect(self.server, self.port, 60)
				break
			except ConnectionRefusedError as e:
				logger.error("Mqtt: connect to %s:%s failed: %s", self.server, self.port, e)
				asyncio.sleep(10)
		await self.wait_connect()


	async def stop(self):
		logger.info("%s done running", self.name)
		self.running = False
		if self.connected.is_set():
			self.mq.disconnect()
			await self.disconnected.wait()


	async def wait_connect(self):
		logger.debug("%s waiting for connect", self.name)
		await self.connected.wait()
		logger.info("%s got connected", self.name)
		for topic in self.subscription_list:
			logger.debug("%s subscribe %s", self.name, topic)
			self.mq.subscribe(topic)
			await self.subscribed.wait()


	def on_connect(self, client, userdata, flags, result):
		logger.info("%s connected %s", self.name, result)
		if result == 0:
			self.disconnected.clear()
			self.connected.set()


	def on_subscribe(self, client, userdata, mid, granted_qos):
		self.subscribed.set()


	def on_disconnect(self, client, userdata, flags):
		self.disconnected.set()
		logger.info("%s: disconnected", self.name)
		self.connected.clear()


	def on_message(self, client, userdata, msg):
		try:
			ddata = json.loads(msg.payload.decode('utf-8'))
		except:
			ddata = msg.payload
		logger.info("%s got %s %s", self.name, msg.topic, ddata)


	def mk_json_msg(self, msg):
		try:
			payload = msg.payload.decode('utf-8')
			jmsg = {'topic': msg.topic, 'payload': payload}
		except:
			jmsg = {'topic': msg.topic, 'raw': msg.payload}

		logger.debug("steamlink msg %s", str(jmsg))
		return jmsg


	def publish(self, firsthop, payload, qos=0, retain=False, sub="control"):
		s = self.control_topic_x if sub == "control" else self.data_topic_x
		topic = s % firsthop
		if 'mqtt' in DBGK:  logger.info("%s publish %s %s", self.name, topic, payload)
		self.mq.publish(topic, payload=payload, qos=qos, retain=retain)


	def public_publish(self, nodename, data, qos=0, retain=False):
		if self.public_topic_data is None:
			return
		topic = self.public_topic_data % nodename
		# logger.debug("%s public publish %s %s", self.name, topic, data)
		self.mq.publish(topic, payload=data, qos=qos, retain=retain)


class Mqtt_Broker(Broker):

	def __init__(self, config=None, loop=None, plugin_namespace=None):
		super().__init__(config, loop, plugin_namespace)
		self.logger.setLevel(logging.WARN)


	async def stop(self):
		await Broker.shutdown(self)
