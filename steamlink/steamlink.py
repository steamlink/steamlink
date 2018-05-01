#!/usr/bin/env python3

# python library Stealink network

import struct
from asyncio import Queue
from queue import Empty, Full
import json
import time
import asyncio
import re
import os

from .timelog import TimeLog

import logging
logger = logging.getLogger(__name__)

from .util import phex

from .linkage import (
	registry,
	Room,
	Item,
)


SL_MAX_MESSAGE_LEN = 255
SL_ACK_WAIT = 3


_MQTT = None
_DB = None

def Attach(mqtt, db):
	global _MQTT, _DB
	_MQTT = mqtt
	_DB = db
	logger.debug("steamlink: Attached apps '%s, %s'", _MQTT.name, _DB.name)


TODO = """
- track routing table from received packets

"""



#
# Exception 
#
class SteamLinkError(Exception):
	def __init__(self, message):

		# Call the base class constructor with the parameters it needs
		super().__init__(message)



SL_RESPONSE_WAIT_SEC = 10
MAX_NODE_LOG_LEN = 1000		# maximum packets stored in per node log


RoomSyntax = """
<ritype>_<key>_<detail>

ritype = Steam, Mesh, Node, Packet
key = ID or *
detail = None or *


Steam_*					-> all (1) root records
Steam_0					-> root record 0
Steam_0_*				-> all meshes

Mesh_*					-> all meshes
Mesh_Mesh000001			-> mesh 1
Mesh_Mesh000001_*		-> all nodes in mesh 1

Node_*					-> all nodes
Node_Node00000001		-> node 1
Node_Node00000001_*		-> all pkt logs for node 1

#PacketType_*				-> all pkt types
#PacketType_ON				-> ??
#PacketType_ON_*			-> all ON packets

Packet_*
Packet_1
Packet_1_*					XXX nothing below pkt

"""

NODEVER = 1
MAXSILENCE = 45

#
# SL_CodeCfgStruct
#
class SL_NodeCfgStruct:
	"""
	Node configuration data, as stored in flash
	defined in SteamLink.h in the steamlink-arduino repo

	struct SL_NodeCfgStruct {
		uint8_t  version;
		uint32_t slid;
		char name[10];
		char description[32];
		float gps_lat;
		float gps_lon;
		short altitude;		// in meters
		uint8_t max_silence; // in seconds
		bool battery_powered;
		uint8_t radio_params; // radio params need to be interpreted by drivers

	"""
	sfmt = '<BL10s32sffhBBB'

	def __init__(self, version = NODEVER, slid = None, name = "*UNK*", description = "*UNK*", gps_lat = 0.0, gps_lon = 0.0, altitude = 0, max_silence = MAXSILENCE, battery_powered = False, radio_params = 0, pkt = None):
		if pkt is None:	 # construct
			self.version = version
			self.slid = slid						# L
			self.name = name						# 10s
			self.description = description			# 32s
			self.gps_lat = gps_lat					# f
			self.gps_lon = gps_lon					# f
			self.altitude = altitude				# h
			self.max_silence = max_silence			# B
			self.battery_powered = battery_powered	# B
			self.radio_params = radio_params		# B

		else:			# deconstruct
			if struct.calcsize(SL_NodeCfgStruct.sfmt) != len(pkt):
				logger.error("NodeCfgStruct: packed messages length incorrect, wanted %s, got %s", struct.calcsize(SL_NodeCfgStruct.sfmt), len(pkt))
				raise SteamLinkError("packed messages length incorrect")
			self.version, self.slid, name, description, self.gps_lat, self.gps_lon, self.altitude, self.max_silence, battery_powered, self.radio_params = struct.unpack(SL_NodeCfgStruct.sfmt, pkt)
			self.name = name.decode().strip('\0')
			self.description = description.decode().strip('\0')
			self.battery_powered = battery_powered == 1

	def pack(self):
		self.pkt = struct.pack(SL_NodeCfgStruct.sfmt, self.version, self.slid, self.name.encode(), self.description.encode(), self.gps_lat, self.gps_lon, self.altitude, self.max_silence, self.battery_powered, self.radio_params)
		return self.pkt


	def __str__(self):
		try:
			return "NodeCFG: %s %s %s" % (self.slid, self.name, self.description)
		except Exception as e:
			return "NodeCFG: undefined: %s" % e

	def save(self):
		d = {
			'slid': self.slid,
			'name': self.name,
			'description': self.description,
			'gps_lat': self.gps_lat,
			'gps_lon': self.gps_lon,
			'altitude': self.altitude,
			'max_silence': self.max_silence,
			'battery_powered': self.battery_powered,
			'radio_params': self.radio_params
		}
		return d



#
# SL_OP op codes
#
class SL_OP:
	'''
	control message types: EVEN, 0 bottom bit
	data message types: ODD, 1 bottom bit
	'''

	DN = 0x30		# data to node, ACK 
	BN = 0x32		# slid precedes payload, bridge forward to node
	GS = 0x34		# get status, reply with SS message
	TD = 0x36		# transmit a test message via radio
	SC = 0x38		# set radio paramter to x, acknowlegde with AK or NK
	BC = 0x3A		# restart node, no reply
	BR = 0x3C		# reset the radio, TBD
	AN = 0x3E		# Ack from store -> node

	DS = 0x31		# data to store
	BS = 0x33		# bridge to store
	ON = 0x35		# send status on to store, send on startup
	AS = 0x37		# acknowlegde the last control message
	NK = 0x39		# negative acknowlegde the last control message
	TR = 0x3B		# Received Test Data
	SS = 0x3D		# status info and counters
	NC = 0x3F		# No Connection or timeout

	def code(code):
		try:
			return list(SL_OP.__dict__.keys())[list(SL_OP.__dict__.values()).index(code)]
		except:
			pass
		return '??'

SL_AN_CODE = {0: 'Success', 1: 'Supressed duplicate pkt', 2: 'Unexpected pkt, dropping'}
SL_AS_CODE = {0: 'Success', 1: 'Supressed duplicate pkt', 2: 'Unexpected pkt, dropping'}

#
# Steam
#
class Steam(Item):
	console_fields = {
 	 "Name": "self.name",
 	 "Meshes": "list(self.children.keys())",
	 "Time": "time.asctime()",
	 "Load": '"%3.1f%%" % self.cpubusy',
	}

	childclass = 'Mesh'
	keyfield = 'key'
	cache = {}

	def find_by_id(Id):
		return registry.find_by_id('Steam', Id)
		if Id in Steam.cache:
			return Steam.cache[Id]
		rec = Steam.db_table.search(Steam.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		logger.debug("find_by_id %s found %s", Id, rec[0])
		n = Steam(rec[0][Steam.keyfield])
		Steam.cache[Id] = n
		return n

	def find_by_id(Id):
		rec = Steam.db_table.search(Steam.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		return rec[0]


	def __init__(self, conf):
		self.desc = conf['description']
		self.autocreate = conf['autocreate']
		self.cpubusy = 0
		self.key = int(conf['id'])
		super().__init__('Steam', int(conf['id']))
		self.keyfield = Steam.keyfield
		_MQTT.set_msg_callback(self.on_data_msg)
		_MQTT.set_public_control_callback(self.on_public_control_msg)
		self.public_topic_control = _MQTT.get_public_control_topic()
		self.public_topic_control_re = self.public_topic_control % "(.*)"


		Steam.db_table = _DB.table('Steam')
		Mesh.db_table = _DB.table('Mesh')
		Node.db_table = _DB.table('Node')
		Packet.db_table = _DB.table('Packet')

		self.mqtt_test_succeeded  = False

		mq_cmd_msg = { "cmd": "boot" }
		_MQTT.publish("store", json.dumps(mq_cmd_msg), sub="data")
		self.write()


	def gen_console_data(self):
		data = {}
		for label in Steam.console_fields:
			source = Steam.console_fields[label]
			try:
				v = eval(source)
			except Exception as e:
				v = "*%s*" % e
			data[label] = v
		return data

	def set_loglevel(self, loglevel):
		from .linkage import logger as linkage_logger 
		from .mqtt import logger as mqtt_logger 
		from .db import logger as db_logger 
		from .testdata import logger as testdata_logger 
		from .web import logger as web_logger

		logger.setLevel(loglevel)
		linkage_logger.setLevel(loglevel)
		mqtt_logger.setLevel(loglevel)
		db_logger.setLevel(loglevel)
		testdata_logger.setLevel(loglevel)
		web_logger.setLevel(loglevel)



	def handle_store_command(self, cmd):
		if type(cmd) != type({}):
			logger.warning("unreadable cmd %s", cmd)
			return
		if cmd['cmd'] == 'boot':
			if self.mqtt_test_succeeded:
				logger.error("there is a second system")
				return
			else:
				logger.debug("mqtt test successfull")
			_MQTT.publish("store", "Store Online", sub="control")
			self.mqtt_test_succeeded = True
		elif cmd['cmd'] == 'debug':
			dbglvl = cmd.get('dbglvl', None)
			slvl = cmd.get('level', None)
			if slvl is not None:
				loglevel = getattr(logging, slvl.upper())
				self.set_loglevel(loglevel)
				logger.warning("setting loglevel to %s", loglevel)
			if dbglvl is not None:
				logging.DBG = int(dbglvl)
		elif cmd['cmd'] == 'shutdown':
			from .__main__ import GracefulExit, GracefulRestart
			if cmd.get('restart',False):
				raise GracefulRestart
			else:
				raise GracefulExit
		else:
			logger.warning("unknown store command %s", cmd)
				

	def on_public_control_msg(self, client, userdata, msg):
		if logging.DBG > 2: logger.debug("on_public_control_msg %s %s", msg.topic, msg.payload)
		match = re.match(self.public_topic_control_re, msg.topic) 
		if match is None:
			logger.warning("topic did not match public control topic: %s %s", topic, self.public_topic_control)
			return
		nodename = match.group(1)

		node = Node.find_by_name(nodename)
		if node is None:
			logger.warning("public control: no such node node %s: %s", nodename, msg.payload)
			return
		node.send_data_to_node(msg.payload+b'\0')


	def on_data_msg(self, client, userdata, msg):
		# msg has  topic, payload, retain

		topic_parts = msg.topic.split('/', 2)
		if topic_parts[1] == "store":
			try:
				cmd = json.loads(msg.payload.decode('utf-8'))
			except:
				cmd = msg.payload
			logger.debug("store command %s", cmd)
			self.handle_store_command(cmd)
			return

		if logging.DBG > 2: logger.debug("on_data_msg  %s %s", msg.topic, msg.payload)
		try:
			sl_pkt = Packet(pkt=msg.payload)
		except SteamLinkError as e:
			logger.warning("mqtt: pkt dropped: '%s', steamlink error %s", msg.payload, e)
			return
		except ValueError as e:
			logger.warning("mqtt: pkt dropped: '%s', value error %s", msg.payload, e)
			return

		node = Node.find_by_id(sl_pkt.slid)
		if node is None:		# Auto-create node
			if not self.autocreate:
				logger.warning("on_data_msg: no node for pkt %s", sl_pkt)
				return
			if sl_pkt.sl_op == SL_OP.ON:
				node = Node(sl_pkt.slid, sl_pkt.nodecfg)
			else:
				logger.warning("on_data_msg: no node for pkt %s", sl_pkt)
				return
		sl_pkt.set_node(node)
		node.post_data(sl_pkt)


	async def start(self):
		process_time = time.process_time()
		now = time.time()
		delta = 0
		wait = 1
		while True:
			await asyncio.sleep(wait)
			self.heartbeat()

			n_process_time = time.process_time()
			n_now = time.time()

			delta = n_now - now
			wait = 1 - (n_now % 1)
			self.cpubusy = ((n_process_time - process_time) / delta ) * 100.0
			now = n_now
			process_time = n_process_time
			if logging.DBG == 0:	# N.B. reduce noise when debuging, i.e. no heartbeat
				self.schedule_update()


	def heartbeat(self):
		if not 'Node' in registry.reg['ItemTypes']:
			return
		n_now = time.time()
		for node in registry.get_all('Node'):
			if node.wait_for_AS['wait'] != 0:
				rwait = int(node.wait_for_AS['wait'] - n_now)
				logger.debug("heartbeat: %s wait %s sec for AS ", node.name, rwait)
				if rwait <= 0:
					pkt = node.wait_for_AS['pkt']
					node.publish_pkt(pkt, resend=True)
					node.set_wait_for_AS(pkt)
					node.wait_for_AS['count'] += 1
			elif node.is_overdue() and node.is_state_up():
				node.set_state("OVERDUE")
				node.schedule_update()
			if not node.is_state_up():		#XXX not offline or sleeping
				if node.last_packet_tx_ts != 0 and node.last_packet_tx_ts + MAXSILENCE < n_now:
					node.send_get_status()


	def save(self, withvirtual=False):
		r = {}
		r['key'] = self.key
		r['name'] = self.name
		r['desc'] = self.desc
		if withvirtual:
			r["Meshes"] = list(self.children.keys())
			r["Time"] = time.asctime()
			r["Load"] = "%3.1f%%" % self.cpubusy
		return r


#
# Mesh
#
class Mesh(Item):
	console_fields = {
	 "mesh_id": "self.mesh_id",
	 "Name": "self.name",
	 "Description": "self.desc",
	 "Total Nodes": "len(self.children)",
	 "Active Nodes": "len(self.children)",
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 }

	childclass = 'Node'
	keyfield = 'mesh_id'
	cache = {}

	def find_by_id(Id):
		return registry.find_by_id('Mesh', Id)
		if Id in Mesh.cache:
			return Mesh.cache[Id]
		rec = Mesh.db_table.search(Mesh.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		logger.debug("find_by_id %s found %s", Id, rec[0])
		n = Mesh(rec[0][Mesh.keyfield])
		Mesh.cache[Id] = n
		return n


	def __init__(self, mesh_id):
		self.mesh_id = int(mesh_id)
		logger.debug("Mesh creating: %s", mesh_id)
		self.packets_sent = 0
		self.packets_received = 0
		self.desc = "Description for mesh %s" % mesh_id

		super().__init__('Mesh', mesh_id, parent_class=Steam, key_in_parent=0)
		self.keyfield = Mesh.keyfield
		self.desc = "Description for %s" % self.name
		logger.info("Mesh created: %s", self)
		self.write()


	def mkname(self):
		return "Mesh%x" % int(self.mesh_id)


	def gen_console_data(self):
		data = {}
		for label in Mesh.console_fields:
			source = Mesh.console_fields[label]
			try:
				v = eval(source)
			except Exception as e:
				v = "*%s*" % e
			data[label] = v
		return data


	def save(self, withvirtual=False):
		r = {}
		r[Mesh.keyfield] = self.mesh_id
		r['name'] = self.name
		r['desc'] = self.desc
		if withvirtual:
			r["Total Nodes"] = len(self.children)
			r["Active Nodes"] = len(self.children)
			r["Packets sent"] = self.packets_sent
			r["Packets received"] = self.packets_received
		return r

#
# Node
#
class Node(Item):
	console_fields = {
 	 "Name": "self.nodecfg.name",
	 "Description": "self.nodecfg.description",
	 "State": "self.state",
	 "Last Pkt received": 'time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_packet_rx_ts)))',
	 "Last Pkt sent": 'time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_packet_tx_ts)))',
	 "Last Node restart": 'time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_node_restart_ts)))',
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 "Packets resent": "self.packets_resent",
	 "Packets dropped": "self.packets_dropped",
	 "Packets missed": "self.packets_missed",
	 "Packets cached": "len(self.children)",
	 "Child 1": "str(self.children[12])",
	 "gps_lat": "self.nodecfg.gps_lat",
	 "gps_lon": "self.nodecfg.gps_lon",
	 "slid": "self.slid",
	}
	UPSTATES = ["ONLINE", "OK", "UP", "TRANSMITTING"]

	childclass = 'Packet'
	keyfield = 'slid'
	cache = {}

	def find_by_id(Id):
		return registry.find_by_id('Node', Id)

		if Id in Node.cache:
			return Node.cache[Id]
		rec = Node.db_table.search(Node.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		logger.debug("Node find_by_id %s found: %s", Id, rec[0])
		n = Node(rec[0][Node.keyfield], rec[0]['nodecfg'])
		Node.cache[Id] = n
		return n
#		return rec[0]


	def find_by_name(name):
		return registry.find_by_name('Node', name)

		if Id in Node.cache:
			return Node.cache[Id]
		rec = Node.db_table.search(Node.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		logger.debug("Node find_by_id %s found: %s", Id, rec[0])
		n = Node(rec[0][Node.keyfield], rec[0]['nodecfg'])
		Node.cache[Id] = n
		return n
#		return rec[0]


	""" a node in a mesh set """
	def __init__(self, slid, nodecfg = None):
		slid = int(slid)
		logger.debug("Node creating : %s" % slid)
		if nodecfg is None:
			self.nodecfg = SL_NodeCfgStruct(slid, "Node%08x" % slid)
			logger.debug("Node config is %s", self.nodecfg)
		elif type(nodecfg) == type({}):
			self.nodecfg = SL_NodeCfgStruct(slid, **nodecfg)
			logger.debug("Node config is %s", self.nodecfg)
		else:
			self.nodecfg = nodecfg
			self.name = nodecfg.name
		self.response_q = Queue(maxsize=1)

		self.slid = slid
		self.mesh_id = (slid >> 8)
		self.packets_sent = 0
		self.packets_received = 0
		self.packets_resent = 0
		self.packets_dropped = 0
		self.packets_missed = 0
		self.pkt_numbers = {True: 0, False:  0}	# next pkt num for data, control pkts
		self.state = "INITIAL"
		self.last_node_restart_ts = 0
		self.last_packet_rx_ts = 0
		self.last_packet_tx_ts = 0
		self.last_packet_num = 0
		self.via = []		# not initiatized
		self.tr = {}		# dict of sending nodes, each holds a list of (pktno, rssi)
		self.wait_for_AS = { 'wait': 0, 'pkt': None, 'count': 0}	# deadline for AS ack
		self.packet_log = TimeLog(MAX_NODE_LOG_LEN)

		self.mesh = Mesh.find_by_id(self.mesh_id)
		if self.mesh is None:		# Auto-create Mesh
			logger.debug("Node %s: mesh %s autocreated", self.slid, self.mesh_id)
			self.mesh = Mesh(self.mesh_id)

		super().__init__('Node', slid, None, parent_class=Mesh, key_in_parent=self.mesh_id)
		self.keyfield = Node.keyfield

		logger.info("Node created: %s" % self)
		self.write()


	def set_pkt_number(self, pkt):
		dc =  pkt.is_data()
		self.pkt_numbers[dc] += 1
		if self.pkt_numbers[dc] == 0:
			self.pkt_numbers[dc] += 1	# skip 0
		return self.pkt_numbers[dc]


	def load(self, data):	#N.B.
		for k in data:
			logger.debug("load %s: %s", k, data[k])
			if k == 'nodecfg':
				self.nodecfg = SL_NodeCfgStruct(**data[k])
			else:
				self.__dict__[k] = data[k]


	def save(self, withvirtual=False):
		r = {}
		r['name'] = self.name
		r[Node.keyfield] = self.slid
		r[Mesh.keyfield] = self.mesh_id
		r['via'] = self.via
		r['nodecfg'] = self.nodecfg.save()
		if withvirtual:
			r['Last Pkt received'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_packet_rx_ts)))
			r['Last Pkt sent'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_packet_tx_ts)))
			r['Last Node restart'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(self.last_node_restart_ts)))
		return r


	def mkname(self):
		if self.nodecfg is not None:
			return self.nodecfg.name
		return "Node%s" % int(self.slid)


	def get_firsthop(self):
		if len(self.via) == 0:
			firsthop = self.slid
		else:
			firsthop = self.via[0]
		return firsthop


	def set_state(self, new_state):
		was_up = self.is_state_up()
		old_state = self.state
		self.state = new_state
		is_state_up = self.is_state_up()

		if not was_up and is_state_up:
			logger.info("node %s now online: %s -> %s", self, old_state, new_state)
		elif was_up and not is_state_up:
			logger.info("node %s now offline: %s -> %s", self, old_state, new_state)
		if was_up != is_state_up:
			# publish node state on some mqtt
			pass

#		if new_state == "TRANSMITTING":		#XXX check if node is sleeping or offline
#			self.send_get_status()


	def is_state_up(self):
		return self.state in Node.UPSTATES


	def is_overdue(self):
		return (self.last_packet_rx_ts + self.nodecfg.max_silence) <= time.time()


	def publish_pkt(self, sl_pkt=None, resend=False, sub="control"):
		if resend:
			logger.debug("resending pkt: %s", sl_pkt)
			self.packets_resent += 1
		else:
			if self.wait_for_AS['wait'] != 0 and sl_pkt.sl_op != SL_OP.AN:
				logger.error("attempt to send pkt while waiting for AS, ignored: %s", sl_pkt)
				self.packets_dropped += 1
				return
		if len(sl_pkt.pkt) > SL_MAX_MESSAGE_LEN:
			logger.error("publish pkt to long(%s): %s", len(sl_pkt.pkt), sl_pkt)
			return
		self.log_pkt(sl_pkt)
		self.packets_sent += 1
		self.mesh.packets_sent += 1
		if logging.DBG > 1: logger.debug("publish_pkt %s to node %s", sl_pkt, self.get_firsthop())
		_MQTT.publish(self.get_firsthop(), sl_pkt.pkt, sub=sub)
		self.last_packet_tx_ts = time.time()
		self.schedule_update()
		self.mesh.schedule_update()
		self.write()


	def send_ack_to_node(self, code):
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.AN, payload=chr(code))	
		self.publish_pkt(sl_pkt)
		return


	def send_boot_cold(self):
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.BC)
		self.publish_pkt(sl_pkt)
		return


	def send_get_status(self):
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.GS)
		self.publish_pkt(sl_pkt)
		return


	def send_data_to_node(self, data): 
		if not self.is_state_up():
			self.packets_dropped += 1
			return SL_OP.NC

		bpayload = data
		logger.debug("send_data_to_node:: len %s, pkt %s", len(bpayload), bpayload)
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.DN, payload=bpayload)
		self.publish_pkt(sl_pkt)
		self.set_wait_for_AS(sl_pkt)
		return


	def send_set_config(self): 
		if not self.is_state_up():
			self.packets_dropped += 1
			return SL_OP.NC
		bpayload = self.nodecfg.pack()
		logger.debug("send_set_config: len %s, pkt %s", len(bpayload), self.nodecfg)
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.SC, payload=bpayload)
		self.publish_pkt(sl_pkt)
		self.set_wait_for_AS(sl_pkt)
		return


	def send_testpacket(self, pkt):
		if not self.is_state_up():
			self.packets_dropped += 1
			return SL_OP.NC
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.TD, payload=pkt)
		self.publish_pkt(sl_pkt)
		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC) # No!
		logger.debug("send_packet %s got %s", sl_pkt, SL_OP.code(rc))
		return rc


	def log_pkt(self, sl_pkt):
		self.packet_log.add(sl_pkt)


	def check_pkt_num(self, sl_pkt):
		pkt_num = sl_pkt.pkt_num
		last_packet_num = self.last_packet_num
		self.last_packet_num = pkt_num
		if last_packet_num == 0:			# we did not see packets from this node before
			return True
		if pkt_num == 0xFFFF:				# wrap
			self.last_packet_num = 0		# remote will skip 0
		if pkt_num == last_packet_num + 1:	# proper sequence
			return True
		if pkt_num == last_packet_num:		# duplicate
			logger.info("%s: received duplicate pkt %s", self, sl_pkt)
			return  sl_pkt.sl_op ==  SL_OP.ON	 # N.B.!
#			return False
		if pkt_num == 1:					# remote restarted
			logger.error("%s: restarted with pkt 1", self)
			return True

		missed = pkt_num-(last_packet_num+1)
		self.packets_missed += missed
		logger.error("%s: %s pkts missed before %s", self, missed, sl_pkt)
		return True	#XXX


	def store_data(self, sl_pkt):
		if sl_pkt.sl_op != SL_OP.DS:		# actual data
			logger.warning("store_data NOT storing non-DS data: %s", sl_pkt.sl_op)
			return
		if logging.DBG > 2: logger.debug("store_data inserting into db")

#		_DB.insert(sl_pkt.save())
		self.send_ack_to_node(0)

		_MQTT.public_publish(self.name, sl_pkt.payload)
		


	def post_data(self, sl_pkt):
		""" handle incoming messages on the ../data topic """
		self.log_pkt(sl_pkt)
		if sl_pkt.is_data():
			if not self.check_pkt_num(sl_pkt):	# duplicate packet
				if sl_pkt.sl_op in [SL_OP.DS]:
					logger.debug("post_data send AN on duplicate DS")
					self.send_ack_to_node(1)
				self.packets_dropped += 1
				return	# duplicate
		else:
			logger.error("%s got control pkt %s", self, sl_pkt)
			self.packets_dropped += 1
			return # NotForUs

		# set ts for all nodes on the route
		for slid in sl_pkt.via + [sl_pkt.slid]:
			node = Node.find_by_id(slid)
			if node:
				node.last_packet_rx_ts = sl_pkt.ts
				if not node.is_state_up():
					node.set_state('TRANSMITTING')
				node.schedule_update()
				node.write()
			else:
				self.packets_dropped += 1
				logger.error("post_data: via node %s not on file", slid)

		# check for routing changes
		if self.via == []:
			self.via = sl_pkt.via
		elif self.via != sl_pkt.via:
			logger.warning("node %s routing changed, was %s is now %s", \
					self, self.via, sl_pkt.via)
			self.via = sl_pkt.via

		self.packets_received += 1
		self.mesh.packets_received += 1

		# logger.info("%s: received %s", self, sl_pkt)

		self.tr[sl_pkt.slid] = sl_pkt.rssi

		sl_op = sl_pkt.sl_op

		if sl_op == SL_OP.ON: # autocreate did set nodecfg
			self.set_wait_for_AS(None)		# give up 
			logger.debug('post_data: slid %d ONLINE', int(self.slid))
			self.nodecfg = SL_NodeCfgStruct(pkt=sl_pkt.bpayload)
			self.send_set_config()
			self.set_state("ONLINE")
			self.last_node_restart_ts = time.time()
			logger.info('%s signed on', self)
		elif sl_op == SL_OP.DS:
#			logger.debug('post_data: slid %d status %s', int(self.slid),sl_pkt.payload)
			self.store_data(sl_pkt)

		elif sl_op == SL_OP.SS:
#			logger.debug("post_data: slid %d status '%s'", int(self.slid),sl_pkt.payload)
			self.set_state(sl_pkt.payload)

		elif sl_op == SL_OP.AS:
			logger.debug('post_data: slid %d ACK:  %s', int(self.slid), 
						SL_AS_CODE[int(sl_pkt.bpayload[0])])
			self.set_wait_for_AS(None)		# done waiting
#			try:
#				self.response_q.put(sl_op)
#			except Full:
#				logger.warning('post_data: node %s queue, dropping: %s', int(self.slid), sl_pkt)
		elif sl_op == SL_OP.TR:
			logger.debug('post_data: node %s test msg', sl_pkt.payload)

			try:
				test_pkt = TestPkt(pkt=sl_pkt.payload)
			except ValueError as e:
				logger.warning("post_incoming: cannot identify test data in %s", sl_pkt.payload)
				self.packets_dropped += 1
				return

			test_pkt.set_receiver_slid(sl_pkt.via)
			test_pkt.set_rssi(sl_pkt.rssi)
			if not test_pkt.pkt['slid'] in self.tr:
				self.tr[test_pkt.pkt['slid']] = []
			self.tr[test_pkt.pkt['slid']].append((test_pkt.pkt['pktno'], test_pkt.pkt['rssi']))
#			sl_log.post_incoming(test_pkt)

		# any pkt from node indicates it's up
		if not self.is_state_up():
			self.set_state('TRANSMITTING')

		self.write()
		self.schedule_update()
		self.mesh.schedule_update()


	def set_wait_for_AS(self, pkt):
		if pkt == None:
			if self.wait_for_AS['pkt'] is None:
				logger.info("wait_for_AS: redundant AS from %s", self)
			else:
				logger.debug("wait_for_AS on %s done", self)
			self.wait_for_AS['wait'] = 0
		else:
			self.wait_for_AS['wait'] = time.time() + SL_ACK_WAIT
			logger.debug("wait_for_AS on %s for %s sec", self, SL_ACK_WAIT)
		self.wait_for_AS['pkt'] = pkt


	def get_response(self, timeout):
		try:
			data = self.response_q.get(timeout=timeout)
		except Empty:
			data = SL_OP.NC
		return data


	def gen_console_data(self):
		data = {}
		for label in Node.console_fields:
			source = Node.console_fields[label]
			try:
				v = eval(source)
			except Exception as e:
				v = "*%s*" % e
			data[label] = v
		return data


#	def console_tail(self, room):
#		v = self.packet_log.get('',-1)
#		r = {
#		  'id': slid,
#		  'type': 'pkt',
#		  'display_vals': { 'data': v }
#		}
#		emit_to_room(r, room)


#	async def console_pkt_log(self, room, slid, count):
#		v = self.packet_log.get(slid, count)
#		r = {
#		  'id': slid,
#		  'type': 'pkt',
#		  'display_vals': v
#		}
#		a_emit_to_room(r, room, self.steam)


#
# Packet
#
class Packet(Item):
	console_fields = {
 	 "op": "SL_OP.code(self.sl_op)",
	 "rssi": "self.rssi",
	 "via": "self.via",
	 "payload": "self.payload",
	 "ts": "time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.ts))",
	}


	PacketID = 0
	data_header_fmt = '<BLHB%is'		# op, slid, pkt_num, rssi, payload"
	control_header_fmt = '<BLH%is'		# op, slid, pkt_num, payload"

	childclass = ''
	keyfield = 'ts'
	cache = {}
	def find_by_id(Id):
		return registry.find_by_id('Packet', Id)
		if Id in Packet.cache:
			return Packet.cache[Id]
		rec = Packet.db_table.search(Packet.keyfield, "==", Id) 
		if rec is None or len(rec) == 0:
			return None
		logger.debug("Node find_by_id %s found: %s", Id, rec[0])
		n = Packet(rec[0][Packet.keyfield])
		Packet.cache[Id] = n
		return n


	def __init__(self, slnode = None, sl_op = None, rssi = 0, payload = None, pkt = None):
		self.rssi = 0
		self.via = []
		self.payload = None
		self.itype = "Packet"
		self.ts = time.time()
#		self.node = None
		self.nodecfg = None
		self.is_outgoing = pkt is None

		if self.is_outgoing:				# construct pkt
			self.construct(slnode, sl_op, rssi, payload)
		else:								# deconstruct pkt
			if not self.deconstruct(pkt):
				logger.error("deconstruct pkt to short: %s", len(pkt))
				raise SteamLinkError("deconstruct pkt to short");
		Packet.PacketID += 1
		super().__init__('Packet', Packet.PacketID, parent_class=Node)
		self.keyfield = Packet.keyfield
		if self.is_outgoing:
			self.set_node(slnode)
		self.write()


	def __str__(self):
		ULOn = "[4m"
		BOn = "[7m"
		BOff = "[0m"
		try:
			return "Packet N%s(%s)%s" % ( self.slid, self.pkt_num, BOn+SL_OP.code(self.sl_op)+BOff)
		except:
			return "Packet NXXX(??)??"


	def set_node(self, node):
		ULOn = "[4m"
		BOn = "[7m"
		BOff = "[0m"
		self.node = node
		self.set_parent(self.slid)
		if self.is_outgoing:
			direction = "send"
			via = "direct" if self.node.via == [] else "via %s" % self.node.via
		else:
			direction = "received"
			via = "direct" if self.via == [] else "via %s" % self.via

		logger.debug("pkt: %s %s %s: %s", ULOn+ direction, via+BOff,  self, self.payload)


	def is_data(self, sl_op = None):
		if sl_op is None:
			sl_op = self.sl_op
		if sl_op is None:
			logger.error("packet op not yet known")
			raise SteamLinkError("packet op not yet known");
		return (sl_op & 0x1) == 1


	def construct(self, slnode, sl_op, rssi, payload):
		self.slid = int(slnode.slid)
		self.sl_op = sl_op
		self.rssi = rssi + 256
		self.payload = payload
		if logging.DBG > 2: logger.debug("SteamLinkPacket payload = %s", payload);
		if self.payload is not None:
			if type(self.payload) == type(b''):
				self.bpayload = self.payload
			else:
				self.bpayload = self.payload.encode('utf8')
		else:
			self.bpayload = b''

		if self.sl_op == SL_OP.ON:
			self.nodecfg = SL_NodeCfgStruct(slid=self.slid)
			logger.debug("Node config is %s", self.nodecfg)
			self.bpayload = self.nodecfg.pack()

		self.pkt_num = slnode.set_pkt_number(self)
		if self.is_data():	# N.B. store never sends data
			sfmt = Packet.data_header_fmt % len(self.bpayload)
			logger.debug("pack: %s %s %s %s %s %s", self.sl_op, self.slid, self.pkt_num, self.rssi, self.bpayload)
			self.pkt = struct.pack(sfmt,
					self.sl_op, self.slid, self.pkt_num, 256 - self.rssi, self.bpayload)
		else:
			sfmt = Packet.control_header_fmt % len(self.bpayload)
			self.pkt = struct.pack(sfmt,
					self.sl_op, self.slid, self.pkt_num, self.bpayload)
			if len(slnode.via) > 0:
				for via in slnode.via[::-1]:
					self.bpayload = self.pkt
					sfmt = Packet.control_header_fmt % len(self.bpayload)
					self.pkt = struct.pack(sfmt, SL_OP.BN, via, 0, self.bpayload)
			if logging.DBG > 1:
				for l in phex(self.pkt, 4):
					logger.debug("pkt c:  %s", l)


	def deconstruct(self, pkt):
		self.pkt = pkt
		if logging.DBG > 1:
			for l in phex(pkt, 4):
				logger.debug("pkt:  %s", l)

		if pkt[0] == SL_OP.BS:		# un-ecap all
			while pkt[0] == SL_OP.BS:
				payload_len = len(pkt) - struct.calcsize(Packet.data_header_fmt % 0)
				sfmt = Packet.data_header_fmt % payload_len
				self.sl_op, slid, self.pkt_num, self.rssi, self.bpayload \
						= struct.unpack(sfmt, pkt)

				self.via.append(slid)
				pkt = self.bpayload
				if logging.DBG > 1: logger.debug("pkt un-ecap BS from P%s(%s)BS, len %s rssi %s", slid, self.pkt_num,  len(pkt), 256-self.rssi)
			self.rssi = self.rssi - 256

		if len(pkt) < struct.calcsize(Packet.data_header_fmt % 0):
			logger.error("deconstruct pkt to short: %s", len(pkt))
			return False;
		if self.is_data(pkt[0]):
			payload_len = len(pkt) - struct.calcsize(Packet.data_header_fmt % 0)
			sfmt = Packet.data_header_fmt % payload_len
			self.sl_op, self.slid, self.pkt_num, rssi, self.bpayload \
						= struct.unpack(sfmt, pkt)
		else:
			payload_len = len(pkt) - struct.calcsize(Packet.control_header_fmt % 0)
			sfmt = Packet.control_header_fmt % payload_len
			self.sl_op, self.slid, self.pkt_num, self.bpayload \
						= struct.unpack(sfmt, pkt)
		self.payload = None

		if self.sl_op == SL_OP.ON:
			try:
				self.nodecfg = SL_NodeCfgStruct(pkt=self.bpayload)
			except SteamLinkError as e:
				logger.error("deconstruct: %s", e)
				return False
			logger.debug("Node config is %s", self.nodecfg)

		if len(self.bpayload) > 0:
			try:
				self.payload = self.bpayload.decode('utf8').strip('\0')
			except Exception as e:
				pass

		return True


	def load(self, data):
		super().load(data)
			

	def save(self, withvirtual=False):
		r = {}
		r['sl_op'] = self.sl_op
		r['pkt_num'] = self.pkt_num
		r['slid'] = self.slid
		r[Packet.keyfield] = self.ts
		r['rssi'] = self.rssi
		r['via'] = self.via
		r['payload'] = self.payload
		r['bpayload'] = repr(self.bpayload)	#??
		if withvirtual:
			r["ts"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.ts))
			r["op"] = SL_OP.code(self.sl_op)
		return r


	def post_data(self):
		self.node.post_data(self)


	def o__str__(self):
		if self.slid is None:
			via = "-%s-" % "??"
		else:
			via = "%d" % self.slid
		if len(self.via) > 0:
			for v in self.via[::-1]: via += "->%d" % v
		s = "SL(op %s, id %s" % (SL_OP.code(self.sl_op), via)
		if self.rssi is not None:
			s += " rssi %s" % (self.rssi)
		if self.payload is not None:
			s += " payload %s" % (self.payload)
		s += ")"
		return s


	def gen_console_data(self):
		data = {}
		for label in Packet.console_fields:
			source = Packet.console_fields[label]
			try:
				v = eval(source)
			except Exception as e:
				v = "*%s*" % e
			data[label] = v
		if logging.DBG > 1: logger.debug("pkt console data: %s", data)
		return data


	def get_room_list(self):
		#logger.debug("get_room_list %s", self)
		rooms = []
		rooms.append( "%s_*" % (self.itype))
		# Packets don't have a 'header' room
		rooms.append( "%s_%s" % (self.itype, self.slid))
		if self.parent is not None:
			rooms.append( "%s_%s_*" % (self.parent.itype, self.parent._key))
		return rooms


#
# TestPkt
#
class TestPkt:
	packet_counter = 1
	def __init__(self, gps=None, text=None, from_slid=None, pkt=None):
		self.pkt = {}
		if text != None and from_slid != None:	# construct pkt
#			self.pkt['lat'] = gps['lat']
#			self.pkt['lon'] = gps['lon']
			self.pkt['slid'] = from_slid
			self.pkt['pktno'] = TestPkt.packet_counter
			self.pkt['text'] = text
			self.pkt['directon'] = 'send'
			TestPkt.packet_counter += 1
		else:									# deconstruct string
			r = pkt.split('|',4)
			self.pkt['lat'] = float(r[0])
			self.pkt['lon'] = float(r[1])
			self.pkt['slid'] = int(r[2])
			self.pkt['pktno'] = int(r[3])
			self.pkt['directon'] = 'recv'
			self.pkt['text'] = r[4]
		ts = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime())
		self.pkt[Packet.keyfield] = ts


	def get_pktno(self):
		return self.pkt['pktno']


	def set_receiver_slid(self, recslid):
		self.pkt['recslid'] = recslid


	def set_rssi(self, rssi):
		self.pkt['rssi'] = rssi


	def pkt_string(self):
		return "%(lat)0.4f|%(lon)0.4f|%(slid)s|%(pktno)s|%(text)s" % self.pkt


	def json(self):
		return json.dumps(self.pkt)


	def __str__(self):
		return "TESTP(%s)" % str(self.pkt)


#
# NodeRoutes
#
class NodeRoutes:
	def __init__(self, dest, via):
		self.dest = dest
		self.via = via


	def __str__(self):
		svia = ""
		for v in self.via:
			svia += "->0x%02x" % v
		return "VIA(%d: %s" % (self.dest, svia)


#
# LogData
#
class LogData:
	""" Handle incoming pkts on the ../data topic """
	def __init__(self, conf):
		self.conf = conf
		self.logfile = open(conf["file"],"a+")
		self.pkt_inq = Queue()
		self.nodes_online = 0


	def log_state(self, slid, new_state):
		logger.debug("logdata node %d %s", slid, new_state)
		self.nodes_online += 1 if new_state == "ONLINE" else -1


	def post_incoming(self, pkt):
		""" a pkt arrives """

		self.log_pkt(pkt, "receive")
		self.pkt_inq.put(pkt, "recv")


	def post_outgoing(self, pkt):
		""" a pkt is sent """
		self.log_pkt(pkt, "send")


	def log_pkt(self, pkt, direction):
		self.logfile.write(pkt.json()+"\n")
		self.logfile.flush()


	def wait_pkt_number(self, pktnumber, timeout, num_packets):
		""" wait for pkt with number pktnumber for a max of timeout seconds """
		lwait = timeout
		packets_seen = 0
		while True:
			now = time.time()
			try:
				test_pkt = self.pkt_inq.get(block=True, timeout=lwait)
				packets_seen += 1
			except Empty:
				test_pkt = None
			logger.debug("wait_pkt_number pkt %s", test_pkt)
			waited = time.time() - now
			if test_pkt and test_pkt.pkt['pktno'] == pktnumber and packets_seen == num_packets:
				return pktnumber
			if waited >= lwait or test_pkt.pkt['pktno'] > pktnumber:	# our pkt will never arrive
				return None
			lwait -= waited


