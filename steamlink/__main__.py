#!/usr/bin/env python3

# Main program for a Stealink network

import sys

import asyncio
import socketio
import signal

import logging
logger = logging.getLogger()

# SteamLink project imports
from steamlink.mqtt import Mqtt

from steamlink.steamlink import (
	Room,
	Steam,
	Mesh,
	Node,
	SL_OP,
	Packet,
	LogData,
	registry,
)

from steamlink.web import (
	WebApp,
)

from steamlink.util import (
	phex,
	getargs,
	loadconfig,
	createconfig,
	daemonize,
	check_pid,
	write_pid,
	closefds_osx,
)

from steamlink.testdata import TestData

from steamlink.const import (
	LIB_DIR,
	PROJECT_PACKAGE_NAME, 
    INDEX_HTML, 
	__version__
)


class GracefulExit(SystemExit):
	code = 1


def raise_graceful_exit():
	raise GracefulExit()



#
# Main
#
def main() -> int:
	""" start steamlinks """

	cl_args = getargs()
	if not cl_args.loglevel:
		if cl_args.debug > 0:
			loglevel = logging.DEBUG
		else:
			loglevel = logging.WARN
	else:
		try:
			loglevel = getattr(logging, cl_args.loglevel.upper())
		except Exception as e:
			print("invalid logging level, use debug, info, warning, error or critical")
			return(1)

	aioloop = asyncio.get_event_loop()

	FORMAT = '%(asctime)-15s: %(levelname)s %(module)s %(message)s'
	logging.basicConfig(format=FORMAT, filename=cl_args.logfile)
	logger.setLevel(loglevel)
	logging.DBG = cl_args.debug
	if logging.DBG >= 2:
		logger.info("DBG: logging all warnings")
		import warnings
		warnings.simplefilter("always")
		logging.captureWarnings(True)
		aioloop.set_debug(enabled=True)

	logger.info("%s version %s" % (PROJECT_PACKAGE_NAME, __version__))
	
	
	# create config  if -C
	conff = cl_args.config 
	if cl_args.createconfig:
		rc = createconfig(conff)
		return(rc)
	
	# load config 
	conf = loadconfig(conff)
	

	try:
		aioloop.add_signal_handler(signal.SIGINT, raise_graceful_exit)
		aioloop.add_signal_handler(signal.SIGTERM, raise_graceful_exit)
	except NotImplementedError:  # pragma: no cover
		# add_signal_handler is not implemented on Windows
		pass

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
	conf_steam = conf.get('Steam',{})

	conf_mqtt = conf.get('mqtt',{})
	
	namespace = conf_general.get('namespace','/sl')

	#sl_log = LogData(conf['logdata'])
	sl_log = None

	logger.debug("startup: create Mqtt")
	mqtt = Mqtt(conf_mqtt, sl_log)

	logger.debug("startup: starting Mqtt")
	aioloop.run_until_complete(mqtt.start())
	
	ping_timeout = conf_general.get('ping_timeout','10')

	logger.debug("startup: create socketio")
	sio = socketio.AsyncServer(
		async_mode = 'aiohttp',
#		cors_allowed_origins =  "http://localhost:* http://127.0.0.1:*", 
#		cors_credentials = True, 
		ping_timeout = ping_timeout,
	)

	logger.debug("startup: create WebApp")
	webapp = WebApp(namespace, sio, conf_console, loop=aioloop)

	logger.debug("startup: create Steam")
	steam = Steam(conf_steam, mqtt, sio)
	steam.attach(webapp, namespace)

	logger.debug("startup: starting coros")
	coros = []
	coros.append(steam.start())
	coros.append(webapp.start())
	coros.append(webapp.qstart())

	if cl_args.testdata:
		logger.debug("startup: create TestData")
		TestTask = TestData(conf['testdata'], aioloop)
		logger.debug("startup: starting TestData")
		coros.append(TestTask.start())
	else:
		TestTask = None

	try:
		aioloop.run_until_complete(asyncio.gather(
			*coros
			))
		aioloop.run_forever()
	except (GracefulExit, KeyboardInterrupt):
		logger.debug("terminating")
	
	
	# Shutdown
	if TestTask:
		logger.debug("stopping TestTask")
		TestTask.stop()
	
	aioloop.run_until_complete(mqtt.stop())
	aioloop.run_until_complete(steam.stop())
	
	logger.info("done")

if __name__ == "__main__":
	sys.exit(main())

