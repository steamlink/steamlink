#!/usr/bin/env python3

# Main program for a Stealink network

import sys
import os
import logging
import struct
import collections
import queue
import json
import time
import yaml
import argparse

import aiomqtt
import asyncio
import socketio
from aiohttp import web


# SteamLink project imports
import steamlink 
import steamlink.const


class SLConsoleNamespace(socketio.AsyncNamespace):
	def on_connect(self, sid, environ):
		logging.debug("SLConsoleNamespace connect %s" % str(environ['REMOTE_ADDR']))

	def on_disconnect(self, sid):
		logging.debug("SLConsoleNamespace disconnect")


	async def on_my_event(self, sid, data):
		logging.debug("SLConsoleNamespace on_my_event %s" % data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=steam.ns)
		return "ACK"

	async def on_need_log(self, sid, data):
		logging.debug("SLConsoleNamespace need_log %s" % data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=steam.ns)
		node = data.get('id',None)
		if  not node in steamlink.Node.name_idx:
			return "NAK"
		try:
			r = steamlink.Node.name_idx[node].console_pkt_log(data['key'], int(data['count']))
		except:
			return "NAK"
		return "ACK"


	async def on_join(self, sid, message):
		logging.debug("SLConsoleNamespace on_join %s" % message)
		self.enter_room(sid, message['room'], namespace=steam.ns)
		room = steamlink.Room(sroom=message['room'])
		if room.lvl == 'Steam':
			await Steam.console_update_full(room)
		elif room.lvl == 'Mesh':
			steamlink.Mesh.console_update_full(room)
		elif room.lvl == 'Node':
			steamlink.Node.console_update_full(room)
		elif room.lvl == 'Pkt':
			steamlink.Node.console_update_tail(room)
		else:
			return "NAK"
		return "ACK"

	async def on_leave(self, sid, message):
		logging.debug("SLConsoleNamespace on_leave %s" % message)
		self.leave_room(sid, message['room'], namespace=steam.ns)
#		await sio.emit('my_response', {'data': 'Left room: ' + message['room']}, room=sid, namespace=steam.ns)
		return "ACK"


#
# Web/Socketio
#
async def index(request):
	index_html = conf_console.get('index',INDEX_HTML)
	with open(index_html) as f:
		return web.Response(text=f.read(), content_type='text/html')


async def config_js(request):
	rj = json.dumps(conf['console'])
	return web.Response(text=rj, content_type='application/json')


async def background_task():
	"""Example of how to send server generated events to clients."""
	count = 0
	while True:
		await sio.sleep(5)
		count += 1
		logging.debug("emit background")
		await sio.emit('my_response', {'data': 'Server generated event'},
				namespace=steam.ns, room="r1")


#
# Utility
#

def phex(p, l=0):
	if type(p) == type(""):
		pp = p.encode()
	else:
		pp = p
	hh = ""
	cc = ""
	head = " " * l
	lines = []
	i = 0
	for c in pp:
		hh += "%02x " % c
		if c >= ord(' ') and pp[i] <= ord('~'):
			cc += chr(c)
		else:
			cc += '.'
		if i % 16 == 15:
			lines.append("%s%s %s" % (head, hh, cc))
			hh = ""
			cc = ""
		i += 1
	if cc != "":
		lines.append("%s%-48s %s" % (head, hh, cc))
	return lines


def getargs():
	parser = argparse.ArgumentParser()
	parser.add_argument("-c", "--config", help="config file, default steamlink.yaml")
	parser.add_argument("-l", "--log", help="set loglevel, default is info")
	parser.add_argument("-C", "--createconfig", help="create a skeleton config file", default=False, action='store_true')
	parser.add_argument("-T", "--testdata", help="generate test data", default=False, action='store_true')
	parser.add_argument("-X", "--debug", help="increase debug level",
					default=0, action="count")
#	parser.add_argument("conf", help="config file to use", default=None)
	return parser.parse_args()


def loadconfig(conf_fname):
	try:
		conf_f = "".join(open(conf_fname, "r").readlines())
		return yaml.load(conf_f)
	except Exception as e:
		print("error: config load: %s" % e)
		sys.exit(1)

def createconfig(conf_fname):
	if os.path.exists(conf_fname):
		print("error: config file '%s' exists, will NOT overwrite with sample!!" % conf_fname)
		sys.exit(1)
	sample_conf = steamlink.const.LIB_DIR + '/steamlink.yaml.sample'
	conf_f = "".join(open(sample_conf, "r").readlines())
	open(conf_fname,"w").write(conf_f)
	print("note: config sample copied to %s" % (conf_fname))
	sys.exit(0)


#
# Web
#
def web_on_cleanup(app):
	logging.info("web closing down")

async def web_on_shutdown(app):
    for ws in app['websockets']:
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    request.app['websockets'].append(ws)
    try:
        async for msg in ws:
            ...
    finally:
        request.app['websockets'].remove(ws)

    return ws

#
# Test
#
class TestData:
	"""" generate  test data in a thread """
	def __init__(self, conf):
		super(TestData, self).__init__()
		self.name = "TestData"
		self.conf = conf
		self.running = True
		logging.info("starting Test Data")


	def stop(self):
		if self.running:
			self.running = False
			logging.debug("%s waiting for shutdown", self.name)


	async def start(self):
		self.nodes = {}
		logging.info("%s task running" % self.name)
		await sio.sleep(conf.get('startwait',1))

		for mesh in range(conf.get('meshes',1)):
			for j in range(conf.get('nodes',1)):
				i = mesh * 256 + j
				self.create_node(i)
				await sio.sleep(0.2)

		for x in range(conf.get('packets',1)):
			for i in range(conf.get('nodes',1)):
				self.create_data(i, "hello from packet %s" % x)
				await sio.sleep(1)

		self.running = False
		logging.debug("%s done", self.name)


	def create_node(self, i):
		logging.debug("sending an ON pkt")
		self.nodes[i] = steamlink.Node(i, nodecfg = None)
		p = steamlink.SteamLinkPacket(self.nodes[i], sl_op = steamlink.SL_OP.ON, payload = None, pkt = None)

		self.nodes[i].publish_pkt(p, "data")
		

	def create_data(self, i, data):
		p = steamlink.SteamLinkPacket(self.nodes[i], sl_op = steamlink.SL_OP.DS, payload = "Hello", pkt = None)
		self.nodes[i].publish_pkt(p, data)


#
# Main
#
nodes = {}
node_routes = {}
locations = {}
radio_param = {}

DBG = 0

cl_args = getargs()
if not cl_args.log:
	if cl_args.debug > 0:
		loglevel = logging.DEBUG
	else:
		loglevel = logging.WARN
else:
	try:
		loglevel = getattr(logging, cl_args.log.upper())
	except Exception as e:
		print("invalid logging level, use debug, info, warning, error or critical")
		sys.exit(1)

FORMAT = '%(asctime)-15s: %(message)s'
logging.basicConfig(format=FORMAT)
logger = logging.getLogger()

logger.setLevel(loglevel)
DBG = cl_args.debug 
logger.DBG = DBG

logger.info("%s version %s" % (steamlink.const.PROJECT_PACKAGE_NAME, steamlink.const.__version__))


# load config 
conff = cl_args.config if cl_args.config else "steamlink.yaml"
if cl_args.createconfig:
	rc = createconfig(conff)
	sys.exit(rc)


conf = loadconfig(conff)
if DBG > 1: print(conf)

conf_general = conf.get('general',{})
conf_console = conf.get('console',{})

#sl_log = steamlink.LogData(conf['logdata'])
sl_log = None

aioloop = asyncio.get_event_loop()
#
try:
	sl_mqtt = steamlink.SteamLinkMqtt(conf['steamlink_mqtt'], sl_log, aioloop)
except KeyError as e:
	logging.error("Gps config key missing: %s", e)
	sys.exit(1)

try:
	aioloop.run_until_complete(sl_mqtt.start())
except KeyboardInterrupt as e:
	print("exit")
	sys.exit(1)
except Exception as e:
	print("setup exception %s" % e)
	sys.exit(2)

steamlink.Node.sl_broker = sl_mqtt

logging.debug("starting socketio")
ping_timeout = conf_general.get('ping_timeout','10')
sio = socketio.AsyncServer(async_mode='aiohttp') #, ping_timeout = ping_timeout) 

app = web.Application()
app['websockets'] = []
#app.router.add_static('/static', 'static')
app.router.add_get('/', index)
app.router.add_get('/config.js', config_js)
app.on_cleanup.append(web_on_cleanup)
app.on_shutdown.append(web_on_shutdown)

# create top level
steam = steamlink.Steam(conf.get('Steam',{}))

sio.attach(app)
sio.register_namespace(SLConsoleNamespace(steam.ns))

aioloop.run_until_complete(steam.start())

if cl_args.testdata:
	TestTask = TestData(conf['testdata'])
	asyncio.run_coroutine_threadsafe(TestTask.start(), aioloop)
else:
	TestTask = None

logging.debug("starting store")

# N.B. need way to stop background task for proper shutdown
#sio.start_background_task(background_task)


host = conf_console.get('host', '127.0.0.1')
port = conf_console.get('port', 8080)
shutdown_timeout = int(conf_console.get('shutdown_timeout','60'))
try:
	web.run_app(app, host=host, port=port, shutdown_timeout=shutdown_timeout)
except KeyboardInterrupt as e:
	print("exit")
except Exception as e:
	logging.warn("general exception %s", e, exc_info=True)

#
# Shutdown
if TestTask:
	logging.debug("stopping TestTask")
	TestTask.stop()

logging.debug("stopping sl_mqtt")
sl_mqtt.stop()

logging.info("done")
