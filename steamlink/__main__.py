#!/usr/bin/env python3

# Main program for a Stealink network

import sys
import os
import struct
import collections
import queue
import json
import time
import yaml

import asyncio
import socketio
from aiohttp import web

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

from steamlink.web import (
	SLConsoleNamespace,
	WebApp,
)


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

	FORMAT = '%(asctime)-15s: %(levelname)s %(module)s %(message)s'
	logging.basicConfig(format=FORMAT, filename=cl_args.logfile)
	logger.setLevel(loglevel)
	logger.DBG = cl_args.debug
	
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

	logger.debug("startup: create socketio")
	sio = socketio.AsyncServer(
		async_mode = 'aiohttp',
#		cors_allowed_origins =  "http://localhost:* http://127.0.0.1:*", 
#		cors_credentials = True, 
#		ping_timeout = ping_timeout) ,
	)
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

	

#	try:
##		app.start()
#		asyncio.run_coroutine_threadsafe(app.start(), aioloop)
#	
#	except KeyboardInterrupt as e:
#		print("exit")
#	except Exception as e:
#		logger.warn("general exception %s", e, exc_info=True)
	
	#
	# Shutdown
	if TestTask:
		logger.debug("stopping TestTask")
		TestTask.stop()
	
	aioloop.run_until_complete(mqtt.stop())
	
	logger.info("done")
	


if __name__ == "__main__":
	sys.exit(main())

