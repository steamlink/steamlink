

import logging
import aiomqtt
import asyncio
import random
import sys
import os
from hbmqtt.broker import Broker

logger = logging.getLogger(__name__)

#
# Mqtt
#
class Mqtt:
	def __init__(self, conf, sl_log, loop = None):
		self.conf = conf
		self.name = "mqtt"
		self.sl_log = sl_log
		if loop is None:
			self.loop = asyncio.get_event_loop()
		else:
			self.loop = loop

		self.topic_prefix = conf.get('prefix', 'SteamLink')
		self.topic_control = conf.get('control', 'control')
		self.topic_data = conf.get('data', 'data')
		self.server =   conf.get('server', '127.0.0.1')
		self.port =     int(conf.get('port', 1883))
		self.clientid = conf.get('clientid', "clie"+"%04i" % int(random.random() * 10000))
		self.username = conf.get('username', None)
		self.password = conf.get('password', None)
		self.ssl_certificate = conf.get('ssl_certificate', None)

		self.control_topic_x = "%s/%%s/%s" % (self.topic_prefix, self.topic_control)
		self.data_topic_x = "%s/%%s/%s" % (self.topic_prefix, self.topic_data)
		self.data_topic = "%s/+/%s" % (self.topic_prefix, self.topic_data)

		self.connected = asyncio.Event(loop=loop)
		self.subscribed = asyncio.Event(loop=loop)
		self.disconnected = asyncio.Event(loop=loop)

		self.mq = aiomqtt.Client(client_id=self.clientid, loop=loop)
		self.mq.loop_start()
#		self.mq.enable_logger(logger)
		if self.ssl_certificate:
			logger.debug("%s: using cert %s", self.name, self.ssl_certificate)
			try:
				self.mq.tls_set(self.ssl_certificate)
			except FileNotFoundError as e:
				logger.error("Mqtt: tls_set certificate %s: %s", self.ssl_certificate, e)
				sys.exit(1)
			self.mq.tls_insecure_set(False)
		if self.username and self.password:
			self.mq.username_pw_set(self.username, self.password)
		self.mq.on_connect = self.on_connect
		self.mq.on_subscribe = self.on_subscribe
		self.mq.on_message = self.on_message
		self.mq.on_disconnect = self.on_disconnect
		self.running = True

		self.subscription_list = [self.data_topic]
		self.mq.message_callback_add(self.data_topic, self.on_data_msg)


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
			self.connected.set()
	

	def on_subscribe(self, client, userdata, mid, granted_qos):
		self.subscribed.set()


	def on_disconnect(self, client, userdata, flags):
		self.disconnected.set()
		logger.info("%s: disconnected", self.name)
		self.connected.clear()


	def on_message(self, client, userdata, msg):
		logger.info("%s got %s %s", self.name, msg,topic, json.loads(msg.payload.decode('utf-8')))


	def mk_json_msg(self, msg):
		try:
			payload = msg.payload.decode('utf-8')
			jmsg = {'topic': msg.topic, 'payload': payload }
		except:
			jmsg = {'topic': msg.topic, 'raw': msg.payload }

		logger.debug("steamlink msg %s", str(jmsg))
		return jmsg


	def on_data_msg(self, client, userdata, msg):
		topic_parts = msg.topic.split('/', 2)
		try:
			sl_pkt = Packet(pkt=msg.payload)
		except:
			return

		sl_id = sl_pkt.slid
		node = registry.find_by_id(sl_id)
		if node is None:
			logger.warning("Mqtt new node with sl_id 0x%0x", sl_id)
			Node(sl_id, steam)
		node.post_data(sl_pkt)
				
	
	def publish(self, firsthop, pkt, qos=0, retain=False, sub="control"):
		s = self.control_topic_x if sub == "control" else self.data_topic_x
		topic = s % firsthop
		logger.info("%s publish %s %s", self.name, topic, pkt)
		self.mq.publish(topic, payload=pkt.pkt, qos=qos, retain=retain)
#		time.sleep(0.1)




class Mqtt_Broker(Broker):

	def __init__(self, config=None, loop=None, plugin_namespace=None):
		print("Config", config)
		super().__init__(config, loop, plugin_namespace)


#	async def start(start):
#		await Broker.start(self)


	async def stop(self):
		await Broker.shutdown(self)

