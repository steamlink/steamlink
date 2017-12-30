import asyncio
import random
import time

from steamlink.steamlink import (
	Steam,
	Mesh,
	Node,
	Packet,
	SL_OP,
)

import logging
logger = logging.getLogger(__name__)

#
# Test
#
class TestData:
	"""" generate  test data in a thread """
	def __init__(self, conf, loop):
		self.name = "TestData"
		self.conf = conf
		self.loop = loop
		self.running = True
#		self.go = asyncio.Event(loop=loop)
		logger.info("starting Test Data")
		self.m = Mesh(0)
		self.meshes = {}
		self.nodes = {}
		self.starttime = None


	def stop(self):
		if self.running:
			self.running = False
			logger.debug("%s waiting for shutdown", self.name)


	async def start(self):
		n_nodes = self.conf.get('nodes',1)
		n_meshes = self.conf.get('meshes',1)
		n_packets = self.conf.get('packets',1)
		pkt_delay = float(self.conf.get('pkt_delay',1))

		logger.info("%s task starting" % self.name)
		await asyncio.sleep(self.conf.get('startwait',1))
		self.starttime = time.time()
		logger.warning("%s test start timing" % self.name)

		for mesh in range(n_meshes):
			logger.debug("creating test mesh %s", mesh)
			self.meshes[mesh] = Mesh(mesh)


		logger.info("%s doing %s nodes", self.name, n_nodes)
		nodelist = {}
		for j in range(n_nodes):
			i = int(random.random() * n_meshes) * 256 + j
			await self.create_node(i)

			await asyncio.sleep(0.2)

		for x in range(n_packets):
			ii = int(random.random() * n_nodes)
			i = list(self.nodes.keys())[ii]
			await self.create_data(i, "hello from packet %s" % x)
			await asyncio.sleep(pkt_delay)

		self.running = False
		duration = time.time() - self.starttime
		logger.warning("%s finished, duration %s sec", self.name, int(duration))


	async def create_node(self, i):
		logger.debug("creating test node %s", i)
		self.nodes[i] = Node(i, nodecfg = None)
		logger.debug("create packet %s", "ON")
		p = Packet(self.nodes[i], sl_op = SL_OP.ON, payload="Online")
		logger.debug("sending ON pkt")
		self.nodes[i].publish_pkt(p, "data")


	async def create_data(self, i, data):
		p = Packet(self.nodes[i], sl_op = SL_OP.DS, payload = "Hello")
		logger.debug("sending DS pkt")
		self.nodes[i].publish_pkt(p, data)



