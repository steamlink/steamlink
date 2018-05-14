#!/usr/bin/env python3

# a Python based Node for SteamLink

import asyncio
import time
import signal
import sys

import logging
logger = logging.getLogger()

from steamlink.pynode  import PyNode, SL_NodeCfgStruct

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
	global running
	while running:
		pkt = await receive_q.get()
		print("Got one", pkt)
		if pkt == "go offline":
			sl_node.set_status("OFFLINE")
			running = False
		receive_q.task_done()


async def produce_outgoing(sl_node):
	global running
	while running:
		await asyncio.sleep(10)
		if running:
			sl_node.send("Hello PyWorld")


async def run(loop):
	global running

	def raise_graceful_exit():
		logger.info("shutdown")
		sl_node.set_status("OFFLINE")
		time.sleep(1)
		raise GracefulExit()

	logger.info("PyNode Test")

	loop.add_signal_handler(signal.SIGINT, raise_graceful_exit)
	nodecfg = SL_NodeCfgStruct(slid=401, name="PyNode401", description="Test Py", 
			gps_lat=44.495499, gps_lon=-80.320706)
	
	# create a Queue to receive incoming messages and start the re
	receive_q = asyncio.Queue()

	# create our Node and register the receive queue with it
	sl_node = PyNode(nodecfg, mqtt_conf, loop)
	sl_node.register_receive_queue(receive_q)
	running = True
	consumer = asyncio.ensure_future(process_incoming(sl_node, receive_q))

	await sl_node.start()	 # go online

	# start outgoing 
	await produce_outgoing(sl_node)

	# exit when the queue closes
#	await receive_q.join()


class GracefulExit(SystemExit):
	code = 1


def setup_logging(loglvl):
#	FORMAT='%(name)s - %(levelname)s - %(message)s'
#	logging.basicConfig(level=loglvl, format=FORMAT)
	logging.basicConfig(level=loglvl)

	logging.DBG = 0
	logging.DBGK = []

setup_logging(logging.INFO)
loop = asyncio.get_event_loop()

loop.run_until_complete(run(loop))
logger.info("done")


