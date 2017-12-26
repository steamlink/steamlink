#!/usr/bin/env python3

# Main program for a Stealink network

import sys
import os
import traceback
import syslog

import asyncio
import socketio
import signal

import logging
logger = logging.getLogger()

# SteamLink project imports
from steamlink.mqtt import (
	Mqtt,
	Mqtt_Broker
)

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

DEFAULT_CONF = {
 	'general': {
		'mqtt_broker': 'mqtt_broker',
		'ping_timeout': 30,
	},
	'Steam': {
		'id': 0,
		'name': 'sample1',
		'namespace': '/sl',
		'description': 'SteamLink Sample',
	},
	'tests': {
	},
	'mqtt': {
#		'clientid': 'CLIENTID',
#		'username': 'USERNAME',
#		'password': 'PASSWORD',
		'server': 'localhost',
		'port': 1883,
#		'ssl_certificate': 'ca.crt',

		'prefix': 'SteamLink',
		'data': 'data',
		'control': 'control',
	},

	'console': {
		'host': '0.0.0.0',
		'port': 5050,
		'shutdown_timeout': 10,        # seconds to wait for web server shutdown
		'namespace': '/sl',
		'prefix': 'SteamLinkWeb',
		'index': 'index.htm',         # root page 
	},
	'mqtt_broker':  {
		'listeners': {
			'default': {
				'type': 'tcp',
				'bind': '0.0.0.0:1883',
			},
			'ws-mqtt': {
				'bind': '127.0.0.1:8080',
				'type': 'ws',
				'max_connections': 10,
			},
		},
		'sys_interval': 10,
		'auth': {
			'allow-anonymous': True,
#			'password-file': os.path.join(os.path.dirname(os.path.realpath(__file__)), 'passwd'),
			'plugins': [
#				'auth_file', 
				'auth_anonymous',
			]
		}
	}
}



#
# Main
#
def steamlink_main() -> int:
	global daemon
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
	logging.DBG = cl_args.debug

	if logging.DBG >= 2:
		logger.info("DBG: logging all warnings")
		import warnings
		warnings.simplefilter("always")
		logging.captureWarnings(True)

	logger.info("%s version %s" % (PROJECT_PACKAGE_NAME, __version__))
	
	
	# create config  if -C
	conff = cl_args.config 
	if cl_args.createconfig:
		rc = createconfig(conff)
		return(rc)
	
	# load config 
	if conff is None:
		conf = DEFAULT_CONF
	else:
		conf = loadconfig(DEFAULT_CONF, conff)

	# Daemon functions
	if cl_args.pid_file:
		check_pid(cl_args.pid_file)
	
	daemon = cl_args.daemon
	if cl_args.daemon:
		if cl_args.verbose:
			print("continuing in background")
		daemonize()
	if cl_args.pid_file:
		write_pid(cl_args.pid_file)

	# N.B. no asyncio before daemon!
	aioloop = asyncio.get_event_loop()
	if logging.DBG >= 2:
		aioloop.set_debug(enabled=True)

	try:
		aioloop.add_signal_handler(signal.SIGINT, raise_graceful_exit)
		aioloop.add_signal_handler(signal.SIGTERM, raise_graceful_exit)
	except NotImplementedError:  # pragma: no cover
		logger.error("main: failed to trap signals")

	conf_general = conf.get('general',{})
	conf_console = conf.get('console',{})
	conf_steam = conf.get('Steam',{})
	conf_broker = conf.get('mqtt_broker',{})
	conf_mqtt = conf.get('mqtt',{})
	
	namespace = conf_general.get('namespace','/sl')

	#sl_log = LogData(conf['logdata'])
	sl_log = None

	if not conf_broker is None: 
		logger.debug("startup: create MQTT Broker")
		mqtt_broker = Mqtt_Broker(conf_broker, loop=aioloop)
		logger.debug("startup: start MQTT Broker")
		aioloop.run_until_complete(mqtt_broker.start())

	logger.debug("startup: create Mqtt")
	mqtt = Mqtt(conf_mqtt, sl_log)

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
	logger.debug("startup: start webapp")
	aioloop.run_until_complete(webapp.start())

	logger.debug("startup: create Steam")
	steam = Steam(conf_steam, mqtt, sio)
	steam.attach(webapp, namespace)

	logger.debug("startup: start steam")
	aioloop.run_until_complete(steam.start())

	coros = []
	if cl_args.testdata:
		testconfigs = conf['tests']
		logger.debug("startup: create TestData")
		if not cl_args.testdata in testconfigs:
			logger.error("testdata: no section '%s' in config file", cl_args.testdata)
			sys.exit(1)
		TestTask = TestData(testconfigs[cl_args.testdata], aioloop)
		logger.debug("startup: starting TestData")
		coros.append(TestTask.start())
	else:
		TestTask = None

	coros.append(webapp.qstart())
	logger.debug("startup: starting coros")
	try:
		aioloop.run_until_complete(asyncio.gather(
			*coros
			))
		aioloop.run_forever()
	except (GracefulExit, KeyboardInterrupt):
		logger.debug("terminating")
	except hbmqtt.errors.NoDataException as e:
		logger.notice("coros run_until: hbmqtt.errors.NoDataException: %s", e)
	
	
	# Shutdown
	if TestTask:
		logger.debug("stopping TestTask")
		TestTask.stop()
	
	aioloop.run_until_complete(mqtt.stop())
	aioloop.run_until_complete(steam.stop())
	if not conf_broker is None: 
		aioloop.run_until_complete(mqtt_broker.stop())
	
	logger.info("done")


daemon = False
def main() -> int:
	try:
		rc = steamlink_main()
	except SystemExit as e:
		rc  = e
		pass
	except:
		rc = 127
		exc_type, exc_value, exc_traceback = sys.exc_info()
		tb = traceback.format_exception(exc_type, exc_value, exc_traceback)
		if not daemon:
			for l in tb:
				print(l.rstrip('\n'))
		else:
			syslog.syslog(syslog.LOG_ERR, ' main failed')
			for l in tb:
				syslog.syslog(syslog.LOG_ERR, ' -> %s' % l.rstrip('\n'))
				logger.error(' -> %s', l.rstrip('\n'))

	sys.exit(rc)
if __name__ == "__main__":
	main()
