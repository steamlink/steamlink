from steamlink import (
	Packet,
	Node,
	SL_OP,
)

import logging
logger = logging.getLogger(__name__)

#
# Test
#
class TestData:
	"""" generate  test data in a thread """
	def __init__(self, conf, sio):
		self.name = "TestData"
		self.conf = conf
		self.sio = sio
		self.running = True
		logger.info("starting Test Data")


	def stop(self):
		if self.running:
			self.running = False
			logger.debug("%s waiting for shutdown", self.name)


	async def start(self):
		self.nodes = {}
		logger.info("%s task running" % self.name)
		await self.sio.sleep(conf.get('startwait',1))

		for mesh in range(conf.get('meshes',1)):
			for j in range(conf.get('nodes',1)):
				i = mesh * 256 + j
				self.create_node(i)
				await self.sio.sleep(0.2)

		for x in range(conf.get('packets',1)):
			for i in range(conf.get('nodes',1)):
				self.create_data(i, "hello from packet %s" % x)
				await self.sio.sleep(1)

		self.running = False
		logger.debug("%s done", self.name)


	def create_node(self, i):
		logger.debug("sending an ON pkt")
		self.nodes[i] = Node(i, nodecfg = None)
		p = Packet(self.nodes[i], sl_op = SL_OP.ON, payload = None, pkt = None)

		self.nodes[i].publish_pkt(p, "data")
		

	def create_data(self, i, data):
		p = Packet(self.nodes[i], sl_op = SL_OP.DS, payload = "Hello", pkt = None)
		self.nodes[i].publish_pkt(p, data)



