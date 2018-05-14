#!/usr/bin/env python3

# Main program for a Steamlink network store

import sys
import os
import pathlib
import traceback
import hbmqtt
import hbmqtt.broker
import syslog
import random
import asyncio
import socketio
import signal
import yaml
from collections import  Mapping, OrderedDict

import logging
import logging.handlers
logger = logging.getLogger()

from .linkage import Attach as linkageAttach

# SteamLink project imports
from .mqtt import (
	Mqtt,
	Mqtt_Broker
)

from .linkage import LogQ
from .linkage import DictTable
from .steamlink import SteamSetup, Steam, Mesh, Node, set_steam_root
from .steamlink import Attach as steamlinkAttach

from .web import WebApp
from .db import DB

from .util import (
	getargs,
	loadconfig,
	createconfig,
	daemonize,
	check_pid,
	write_pid,
)

from .testdata import TestData

from .const import (
	PROJECT_PACKAGE_NAME,
	__version__,
	DEFAULT_CONFIG_FILE,
)


class GracefulExit(SystemExit):
	code = 1

class GracefulRestart(SystemExit):
	code = 2

def raise_graceful_restart():
	logger.info("restarting")
	raise GracefulRestart()

def raise_graceful_exit():
	logger.info("shutdown")
	raise GracefulExit()

home = str(pathlib.Path.home())	

		

#
# Default config
# specified config file (if any) will get merged in here
DEFAULT_CONF = OrderedDict({
 	'general': OrderedDict({
		'mqtt_broker': 'mqtt_broker',
		'ping_timeout': 30,
		'working_dir': home + '/.steamlink',
		'max_log_records': 1000
	}),
	'Steam': OrderedDict({
		'id': 0,
		'name': 'sample1',
		'description': 'SteamLink Sample',
		'namespace': '/sl',
		'autocreate': True,
	}),
	'tests': OrderedDict({
		'test1': OrderedDict({
			'desc': 'Test1',
		    'startwait': 2,
		    'meshes': 3,
		    'nodes': 12,
		    'packets': 1000,
		    'pkt_delay': 0.0,
		})
	}),
	'mqtt': OrderedDict({
		'clientid': None,
		'username': None,
		'password': None,
		'server': '127.0.0.1',
		'port': 1883,
		'ssl_certificate': None,

		'prefix': 'SteamLink',
		'data': 'data',
		'control': 'control',
		'public_control': 'SteamLink/pub/%s/control',
		'public_data': 'SteamLink/pub/%s/data',
	}),

	'console': OrderedDict({
		'host': '0.0.0.0',
		'port': 5050,
		'shutdown_timeout': 10,        # seconds to wait for web server shutdown
		'namespace': '/sl',
		'minupdinterval': 1.0,
		'index': "",           # root page
        'ssl_certificate': None,
        'ssl_key': None,
		'repo_name': 'steamlink',
		'repo_owner': 'steamlink',
		'repo_key': 'SteamLinkIsIoT',
		'repo_ci_command': None,
	}),
	'mqtt_broker':  OrderedDict({
		'listeners': OrderedDict({
			'default': OrderedDict({
				'type': 'tcp',
				'bind': '127.0.0.1:1883',
			}),
			'ws-mqtt': OrderedDict({
				'bind': '127.0.0.1:8080',
				'type': 'ws',
				'max_connections': 10,
			}),
		}),
		'sys_interval': 10,
		'auth': OrderedDict({
			'allow-anonymous': True,
			'#password-file': os.path.join(os.path.dirname(os.path.realpath(__file__)), 'passwd'),
			'plugins': [
				'#auth_file',
				'auth_anonymous',
			]
		})
	}),
	'DB':  OrderedDict({
		'db_filename': home + '/.steamlink/steamlink.db',
	})
})


#
# Main
#
def steamlink_main(cl_args, conf):
	global daemon, conf_working_dir, logq

	""" start steamlink """

	if conf['mqtt']['clientid'] is None:
		conf['mqtt']['clientid'] = "clie"+"%04i" % int(random.random() * 10000)

	conf_general = conf['general']
	conf_working_dir = conf_general['working_dir']
	if not os.path.exists(conf_working_dir):
		os.mkdir(conf_working_dir)

	conf_console = conf['console']
	conf_steam = conf['Steam']
	conf_db = conf['DB']

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
		aioloop.add_signal_handler(signal.SIGHUP, raise_graceful_restart)
		aioloop.add_signal_handler(signal.SIGINT, raise_graceful_exit)
		aioloop.add_signal_handler(signal.SIGTERM, raise_graceful_exit)
	except NotImplementedError:  # pragma: no cover
		logger.error("main: failed to trap signals")

	broker_c = conf_general.get('mqtt_broker', None)
	if broker_c is None:
		conf_broker = None
	else:
		conf_broker = conf.get(broker_c, None)
		if conf_broker is None:
			logger.error("mqtt broker section '%s' does not exist", broker_c)
			sys.exit(1)
	
	conf_mqtt = conf['mqtt']

	namespace = conf_steam['namespace']


	# confugure subsystems
	coros = []

	LogQ._table = DictTable(LogQ, keyfield="name", index = OrderedDict())
	logq = LogQ(conf, aioloop)
	coros.append(logq.start())

	msghandler = logging.StreamHandler(logq)
	QFORMAT = '%(levelname)s %(module)s %(message)s'
	qfmt = logging.Formatter(QFORMAT)
	msghandler.setFormatter(qfmt)
	msghandler.setLevel(logging.INFO)
	logger.addHandler(msghandler)

	logger.info("%s version %s" % (PROJECT_PACKAGE_NAME, __version__))

	if conf_broker is not None:
		logger.debug("startup: create MQTT Broker")
		mqtt_broker = Mqtt_Broker(conf_broker, loop=aioloop)
		logger.debug("startup: start MQTT Broker")
		try:
			aioloop.run_until_complete(mqtt_broker.start())
		except hbmqtt.broker.BrokerException as e:
#			logger.error("mqtt: broker start failed: %s", e)
			sys.exit(1)

	logger.debug("startup: create Mqtt")
	mqtt = Mqtt(conf_mqtt)

	aioloop.run_until_complete(mqtt.start())

	ping_timeout = conf_general['ping_timeout']

	ll = logging.getLogger('AsyncServer')
	# use different logging level socketio/engineio modules
	ll.setLevel(logging.WARN)
	logger.debug("startup: create socketio")
	sio = socketio.AsyncServer(
		logger = ll,
		async_mode = 'aiohttp',
#		cors_allowed_origins =  "http://localhost:* http://127.0.0.1:*",
#		cors_credentials = True,
		ping_timeout = ping_timeout,
		engineio_logger = ll,
	)

	logger.debug("startup: open DB")
	db = DB(conf_db, loop=aioloop)

	logger.debug("startup: create WebApp")
	webapp = None
	webapp = WebApp(namespace, sio, conf_console, loop=aioloop)

	linkageAttach(webapp, db)
	steamlinkAttach(mqtt, db)

	logger.debug("startup: start db")
	aioloop.run_until_complete(db.start())

	logger.debug("startup: start webapp")
	aioloop.run_until_complete(webapp.start())
	
	SteamSetup()
	steam = Steam(conf_steam)
	set_steam_root(steam)

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

	ll = logging.getLogger('asyncio_socket')
	ll.setLevel(logging.WARN)

	coros.append(steam.start())

	coros.append(webapp.qstart())
	logger.debug("startup: starting coros")
	restart = False
	try:
		aioloop.run_until_complete(asyncio.gather(
			*coros
			))
		aioloop.run_forever()
	except (GracefulRestart):
		logger.info("restarting")
		restart = True
	except (GracefulExit, KeyboardInterrupt):
		logger.info("shutting down")
	except hbmqtt.errors.NoDataException as e:
		logger.notice("coros run_until: hbmqtt.errors.NoDataException: %s", e)

	# Shutdown
	webapp.stop()
	aioloop.run_until_complete(db.stop())
	if TestTask:
		logger.debug("stopping TestTask")
		TestTask.stop()

	aioloop.run_until_complete(mqtt.stop())
	if not conf_broker is None:
		aioloop.run_until_complete(mqtt_broker.stop())

	logger.info("done")
	return restart


#
# Main
#

daemon = False
def steamlink_command():
	global daemon
	cl_args = getargs()

	if cl_args.version:
		print("%s version %s" % (PROJECT_PACKAGE_NAME, __version__))
		return 0

	try:
		loglevel = getattr(logging, cl_args.loglevel.upper())
	except Exception as e:
		loglevel = None

	if loglevel is None:
		loglevel = logging.DEBUG if cl_args.debug > 0 else logging.INFO

	FORMAT = '%(asctime)-15s: %(levelname)s %(module)s %(message)s'
#	logging.basicConfig(format=FORMAT)
	logger.setLevel(loglevel)
	if cl_args.logfile is not None:
		logging.console = False
		handler = logging.handlers.RotatingFileHandler(
              cl_args.logfile, maxBytes=10**7, backupCount=3)
	else:
		logging.console = True
		handler = logging.StreamHandler()

	fmt = logging.Formatter(FORMAT)
	handler.setFormatter(fmt)
	logger.addHandler(handler)

	DBG = cl_args.debug
	logging.DBG = DBG

	DBGK = cl_args.debugkey
	logging.DBGK = DBGK

	if DBG >= 3:
		logger.info("DBG: logging all warnings")
		import warnings
		warnings.simplefilter("always")
		logging.captureWarnings(True)

	if cl_args.config is None:
		conff = home + "/" + DEFAULT_CONFIG_FILE
	else:
		conff = cl_args.config

	# create config  if -C
	if cl_args.createconfig:
		rc = createconfig(conff, DEFAULT_CONF)
		return(rc)

	# load config
	conf = loadconfig(DEFAULT_CONF, conff)

	try:
		restart = steamlink_main(cl_args, conf)
		rc = 0
#	except SystemExit as e:
#		rc  = e
#		pass
	except:
		restart = False
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
	if restart:
		os.execve(sys.argv[0], sys.argv, os.environ)
		print("os.execve returned")
	return rc

if __name__ == "main":
	rc = steamlink_command()
	sys.exit(rc)
