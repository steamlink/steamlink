import asyncio
import random
import time

from .steamlink import (
	Steam,
	Mesh,
	Node,
	Packet,
	SL_OP,
)

from .linkage import (
	registry,
	Room,
	Item,
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
		self.meshes = {}
		self.nodes = {}
		self.starttime = None


	def stop(self):
		if self.running:
			self.running = False
			logger.debug("%s waiting for shutdown", self.name)


	async def send_test_start(self):
		await asyncio.sleep(self.conf.get('startwait',1))
		await self.send_test(272, "TESTDATA")
		logger.warning("test done")
		return

	async def start(self):
		n_nodes = self.conf.get('nodes',1)
		d_nodes = self.conf.get('del_nodes',0)
		n_meshes = self.conf.get('meshes',1)
		d_meshes = self.conf.get('del_meshes',0)
		n_packets = self.conf.get('packets',1)
		pkt_delay = float(self.conf.get('pkt_delay',0))
		node_delay = float(self.conf.get('node_delay',0))

		logger.info("%s task starting" % self.name)
		await asyncio.sleep(self.conf.get('startwait',1))
		self.starttime = time.time()
		logger.warning("%s test start timing" % self.name)

		for mesh in range(n_meshes):
			logger.debug("creating test mesh %s", mesh)
			self.meshes[mesh] =  registry.find_by_id('Mesh', mesh)
			if self.meshes[mesh] is None:
				self.meshes[mesh] = Mesh(mesh)


		logger.info("%s doing %s nodes", self.name, n_nodes)

		for j in range(n_nodes):
			i = int(random.random() * n_meshes) * 256 + j
			await self.create_node(i)
			await asyncio.sleep(node_delay)

		for x in range(n_packets):
			ii = int(random.random() * n_nodes)
			i = list(self.nodes.keys())[ii]
			await self.create_data(i, "hello from packet %s" % x)
			await asyncio.sleep(pkt_delay)


		for x in range(min(d_nodes, len(self.nodes))):
			i = list(self.nodes.keys())[x]
			logger.info("%s deleting node %s", self.name, i)

			self.nodes[i].delete()
			del self.nodes[i]

		for x in range(min(d_meshes, len(self.meshes))):
			logger.info("%s deleting mesh %s", self.name, x)
			self.meshes[x].delete()
			del self.meshes[x]

		self.running = False
		duration = time.time() - self.starttime
		logger.warning("%s finished, duration %s sec", self.name, int(duration))


	async def create_node(self, i):
		logger.debug("creating test node %s", i)
		self.nodes[i] =  registry.find_by_id('Node', i)
		if self.nodes[i] is None:
			self.nodes[i] = Node(i, nodecfg = None)
			logger.debug("create packet %s", "ON")
		p = Packet(self.nodes[i], sl_op = SL_OP.ON)
		logger.debug("sending ON pkt")
		self.nodes[i].publish_pkt(p, "data")


	async def create_data(self, i, data):
		p = Packet(self.nodes[i], sl_op = SL_OP.DS, payload = "Hello")
		logger.debug("sending DS pkt")
		self.nodes[i].publish_pkt(p, data)


	async def send_test(self, slid, data):
		node = registry.find_by_id('Node', slid)
		if node is None:
			logger.error("no node %s", slid)
			return

		logger.warning("sending %s to node %s", data, node)
		while not node.is_up(): 
			logger.warning("node %s not up, waiting", node)
			await asyncio.sleep(1) 
		rc = node.send_testpacket(data)
		logger.warning("testpkt sent to node %s, code %s", slid, rc)
		
