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

import asyncio
import socketio
from aiohttp import web


# SteamLink project imports
from steamlink import (
	Room,
	Steam,
	Mesh,
	Node,
	SL_OP,
	Packet,
	LogData,
	Mqtt,
)

from steamlink.testdata import TestData

from steamlink.const import (
	LIB_DIR,
	PROJECT_PACKAGE_NAME, 
    INDEX_HTML, 
	__version__
)


class SLConsoleNamespace(socketio.AsyncNamespace):
	def __init__(self, steam):
		self.steam = steam
		super().__init__(self.steam.ns)
		logger.debug("SLConsoleNamespace registered for ns %s", self.steam.ns)


	def on_connect(self, sid, environ):
		logger.debug("SLConsoleNamespace connect %s",str(environ['REMOTE_ADDR']))

	def on_disconnect(self, sid):
		logger.debug("SLConsoleNamespace disconnect")


	async def on_my_event(self, sid, data):
		logger.debug("SLConsoleNamespace on_my_event %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.steam.ns)
		return "ACK"

	async def on_need_log(self, sid, data):
		logger.debug("SLConsoleNamespace need_log %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.steam.ns)
		node = data.get('id',None)
		if  not node in Node.name_idx:
			return "NAK"
		try:
			r = Node.name_idx[node].console_pkt_log(data['key'], int(data['count']))
		except:
			return "NAK"
		return "ACK"


	async def on_join(self, sid, message):
		logger.debug("SLConsoleNamespace on_join %s", message)
		self.enter_room(sid, message['room'], namespace=self.steam.ns)
		room = Room(sroom=message['room'])
		if room.lvl == 'Steam':
			await Steam.console_update_full(room)
		elif room.lvl == 'Mesh':
			await Mesh.console_update_full(room)
		elif room.lvl == 'Node':
			await Node.console_update_full(room)
		elif room.lvl == 'Pkt':
			await Node.console_update_tail(room)
		else:
			return "NAK"
		return "ACK"

	async def on_leave(self, sid, message):
		logger.debug("SLConsoleNamespace on_leave %s", message)
		self.leave_room(sid, message['room'], namespace=self.steam.ns)
#		await sio.emit('my_response', {'data': 'Left room: ' + message['room']}, room=sid, namespace=self.steam.ns)
		return "ACK"


#
# Socketio
#

async def background_task():
	"""Example of how to send server generated events to clients."""
	count = 0
	while True:
		await sio.sleep(5)
		count += 1
		logger.debug("emit background")
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
	parser.add_argument("-c", "--config", 
							help="config file default steamlink.yaml",
							default="steamlink.yaml")
	parser.add_argument("-d", "--daemon", 
							help="excute as a daemon",
							default=False, action='store_true')
	parser.add_argument("-l", "--log", 
							help="set loglevel, default is info", 
							default="info")
	parser.add_argument("-C", "--createconfig", 
							help="create a skeleton config file",
							default=False, action='store_true')
	parser.add_argument("-p", "--pid-file", 
							help="path to pid file when running as daemon", 
							default=None)
	parser.add_argument("-T", "--testdata", 
							help="generate test data",
							default=False, action='store_true')
	parser.add_argument("-v", "--verbose", 
							help="print some info",
							default=False, action='store_true')
	parser.add_argument("-X", "--debug", 
							help="increase debug level",
							default=0, action="count")
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
	sample_conf = LIB_DIR + '/steamlink.yaml.sample'
	conf_f = "".join(open(sample_conf, "r").readlines())
	open(conf_fname,"w").write(conf_f)
	print("note: config sample copied to %s" % (conf_fname))
	sys.exit(0)


#
# WebApp
#
class WebApp(object):

	def __init__(self, steam, sio, conf, loop = None):
		self.name = "WebApp"
		self.conf = conf
		self.sio = sio
		self.steam = steam
		if loop is None:
			self.loop = asyncio.get_event_loop()
		else:
			self.loop = loop
		self.app = web.Application()
		self.app['websockets'] = []
		#self.app.router.add_static('/static', 'static')
		self.app.router.add_get('/', self.index)
		self.app.router.add_get('/config.js', self.config_js)
		self.app.router.add_get('/main.js', self.main_js)
		self.app.on_cleanup.append(self.web_on_cleanup)
		self.app.on_shutdown.append(self.web_on_shutdown)

		self.api_password = conf.get('api_password', None)
		self.ssl_certificate = conf.get('ssl_certificate', None)
		self.ssl_key = conf.get('ssl_key', None)
		self.host = conf.get('host', '127.0.0.1')
		self.port = conf.get('port', 8080)
		self._handler = None
		self.server = None


	def start(self):
		logger.info("%s starting, server %s port %s", self.name, self.host,  self.port)
		self.sio.attach(self.app)
		self.sio.register_namespace(SLConsoleNamespace(self.steam))
		shutdown_timeout = int(self.conf.get('shutdown_timeout','60'))

#		self.loop.run_until_complete(app.startup())
		web.run_app(self.app,
			host=self.host,
			port=self.port,
			shutdown_timeout=shutdown_timeout,
			loop=self.loop)
	

	async def index(self, request):
		index_html = self.conf.get('index',INDEX_HTML)
		with open(index_html) as f:
			return web.Response(text=f.read(), content_type='text/html')


	async def main_js(self, request):
		fname = LIB_DIR + "/html/main.js"
		with open(fname) as f:
			return web.Response(text=f.read(), content_type='application/javascript')


	async def config_js(self, request):
		rj = json.dumps(self.conf)
		return web.Response(text=rj, content_type='application/json')


	def web_on_cleanup(self, app):
		logger.info("web closing down")


	async def web_on_shutdown(self, app):
		for ws in self.app['websockets']:
			await ws.close(code=WSCloseCode.GOING_AWAY,
						   message='Server shutdown')

#	async def websocket_handler(self, request):
#		ws = web.WebSocketResponse()
#		await ws.prepare(request)
#	
#		request.self.app['websockets'].append(ws)
#		try:
#			async for msg in ws:
#				...
#		finally:
#			request.self.app['websockets'].remove(ws)
#	
#		return ws
#


def setup_logging(loglevel, debuglvl):
	global logger
	FORMAT = '%(asctime)-15s: %(message)s'
	logging.basicConfig(format=FORMAT)
	logger = logging.getLogger()
	
	logger.setLevel(loglevel)
	logger.DBG = debuglvl
	
# borrowed from homeassistant
def daemonize() -> None:
	"""Move current process to daemon process."""
	# Create first fork
	pid = os.fork()
	if pid > 0:
		sys.exit(0)

	# Decouple fork
	os.setsid()

	# Create second fork
	pid = os.fork()
	if pid > 0:
		sys.exit(0)

	# redirect standard file descriptors to devnull
	infd = open(os.devnull, 'r')
	outfd = open(os.devnull, 'a+')
	sys.stdout.flush()
	sys.stderr.flush()
	os.dup2(infd.fileno(), sys.stdin.fileno())
	os.dup2(outfd.fileno(), sys.stdout.fileno())
	os.dup2(outfd.fileno(), sys.stderr.fileno())


def check_pid(pid_file: str) -> None:
	"""Check that HA is not already running."""
	# Check pid file
	try:
		pid = int(open(pid_file, 'r').readline())
	except IOError:
		# PID File does not exist
		return

	# If we just restarted, we just found our own pidfile.
	if pid == os.getpid():
		return

	try:
		os.kill(pid, 0)
	except OSError:
		# PID does not exist
		return
	print('Fatal Error: HomeAssistant is already running.')
	sys.exit(1)


def write_pid(pid_file: str) -> None:
	"""Create a PID File."""
	pid = os.getpid()
	try:
		open(pid_file, 'w').write(str(pid))
	except IOError:
		print('Fatal Error: Unable to write pid file {}'.format(pid_file))
		sys.exit(1)


def closefds_osx(min_fd: int, max_fd: int) -> None:
	"""Make sure file descriptors get closed when we restart.

	We cannot call close on guarded fds, and we cannot easily test which fds
	are guarded. But we can set the close-on-exec flag on everything we want to
	get rid of.
	"""
	from fcntl import fcntl, F_GETFD, F_SETFD, FD_CLOEXEC

	for _fd in range(min_fd, max_fd):
		try:
			val = fcntl(_fd, F_GETFD)
			if not val & FD_CLOEXEC:
				fcntl(_fd, F_SETFD, val | FD_CLOEXEC)
		except IOError:
			pass



#
# Main
#
def main() -> int:
	""" start steamlinks """

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
			return(1)
	
	setup_logging(loglevel, cl_args.debug)
#	if cl_args.debug > 1:
#		asyncio.AbstractEventLoop.set_debug(enabled=True)

	if cl_args.verbose:
		print("%s version %s" % (PROJECT_PACKAGE_NAME, __version__))
	
	
	# create config  if -C
	conff = cl_args.config 
	if cl_args.createconfig:
		rc = createconfig(conff)
		return(rc)
	
	# load config 
	conf = loadconfig(conff)
	
	# Daemon functions
	if cl_args.pid_file:
		check_pid(cl_args.pid_file)
	if cl_args.daemon:
		if cl_args.verbose:
			print("continuing in background")
		daemonize()
	if cl_args.pid_file:
		write_pid(cl_args.pid_file)

	conf_general = conf.get('general',{})
	conf_console = conf.get('console',{})
	conf_mqtt = conf.get('mqtt',{})
	
	#sl_log = LogData(conf['logdata'])
	sl_log = None
	aioloop = asyncio.get_event_loop()

	logger.debug("startup: create Mqtt")
	mqtt = Mqtt(conf_mqtt, sl_log)
	logger.debug("startup: starting Mqtt")
	aioloop.run_until_complete(mqtt.start())
	
	ping_timeout = conf_general.get('ping_timeout','10')

	logging.debug("startup: create socketio")
	sio = socketio.AsyncServer(async_mode='aiohttp') #, ping_timeout = ping_timeout) 
	logger.debug("startup: create Steam")
	steam = Steam(conf.get('Steam',{}), mqtt, sio)
	Steam.root = steam	## !!
	logger.debug("startup: create WebApp")
	app = WebApp(steam, sio, conf_console, loop=aioloop)

	logger.debug("startup: starting steam")
	aioloop.run_until_complete(steam.start())
	
	if cl_args.testdata:
		logger.debug("startup: create TestData")
		TestTask = TestData(conf['testdata'], sio)
		logger.debug("startup: starting TestData")
#		aioloop.run_until_complete(TestTask.start())
		asyncio.run_coroutine_threadsafe(TestTask.start(), aioloop)
	else:
		TestTask = None
	
	aioloop.run_until_complete(app.start())
	logger.debug("startup: web app started")

	
	
	# N.B. need way to stop background task for proper shutdown
	#sio.start_background_task(background_task)
	

#	try:
##		app.start()
#		asyncio.run_coroutine_threadsafe(app.start(), aioloop)
#	
#	except KeyboardInterrupt as e:
#		print("exit")
#	except Exception as e:
#		logging.warn("general exception %s", e, exc_info=True)
	
	#
	# Shutdown
	if TestTask:
		logging.debug("stopping TestTask")
		TestTask.stop()
	
	aioloop.run_until_complete(mqtt.stop())
	
	logging.info("done")
	


if __name__ == "__main__":
	sys.exit(main())

