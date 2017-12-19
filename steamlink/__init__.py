#!/usr/bin/env python3

# Control program for a Stealink network

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


TODO = """
- track routing table from received packets

"""

SL_RESPONSE_WAIT_SEC = 10
MAX_NODE_LOG_LEN = 1000		# maximum packets stored in per node log


RoomSyntax = """
<lvl>_<key>_<detail>

lvl = Steam, Mesh, Node, Pkt
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

#PktType_*				-> all pkt types
#PktType_ON				-> ??
#PktType_ON_*			-> all ON packets

Pkt_*
Pkt_1
Pkt_1_*					XXX nothing below pkt

"""


class Room:
	Lvls = ['Steam', 'Mesh', 'Node', 'Pkt']
	def __init__(self, lvl = None, key = None, detail = None, sroom = None):
		if sroom:
			l = sroom.split('_')
			assert len(l) >= 2 and len(l) <= 3, "room string invalid: %s" % sroom
			self.lvl = l[0]
			self.key = l[1]
			self.detail = None if len(l) < 3 else l[2]
		else:
			self.lvl = lvl
			self.key = key
			self.detail = detail
		assert self.lvl in Room.Lvls , "room key invalid: %s" % sroom


	def is_item_room(self):
		return self.detail != None


	def is_header(self):
		if self.detail == '*' or self.key == '*':
			return False
		return True

	def no_key(self):
		return "%s_*" % (self.lvl)
		
	def __str__(self):
		if self.detail:
			return "%s_%s_%s" % (self.lvl, self.key, self.detail)
		return "%s_%s" % (self.lvl, self.key)
		

		
class SL_NodeCfgStruct:
	""" 
	Node configuration data, as stored in flash

	struct SL_NodeCfgStruct {
		int slid;
		char name[10];
		char description[32];
		float gps_lat;
		float gps_lon;
		short altitude;
		short max_silence;
		boolean sleeps;
		boolean pingable;
		boolean battery_powered;
		byte radio_params;
	}
	"""
	sfmt = '<L10s32sffhhBBBB'

	def __init__(self, slid = None, name = "*UNK*", description = "*UNK*", gps_lat = 0.0, gps_lon = 0.0, altitude = 0, max_silence = 60, sleeps = False, pingable = True, battery_powered = False, radio_params = 0, pkt = None):
		if pkt == None:	 # construct
			self.slid = slid						# L
			self.name = name						# 10s
			self.description = description			# 32s
			self.gps_lat = gps_lat					# f
			self.gps_lon = gps_lon					# f
			self.altitude = altitude				# h
			self.max_silence = max_silence			# h
			self.sleeps = sleeps					# B
			self.pingable = pingable				# B
			self.battery_powered = battery_powered	# B
			self.radio_params = radio_params		# B 
	
		else:			# deconstruct
			assert struct.calcsize(SL_NodeCfgStruct.sfmt) == len(pkt)
			self.slid, name, description, self.gps_lat, self.gps_lon, self.altitude, self.max_silence, sleeps, pingable, battery_powered, self.radio_params = struct.unpack(SL_NodeCfgStruct.sfmt, pkt)
			self.name = name.decode().strip('\0')
			self.description = description.decode().strip('\0')
			self.pingable = pingable == 1
			self.battery_powered = battery_powered == 1
			self.sleeps = sleeps == 1

	def pack(self):
		self.pkt = struct.pack(SL_NodeCfgStruct.sfmt, self.slid, self.name.encode(), self.description.encode(), self.gps_lat, self.gps_lon, self.altitude, self.max_silence, self.sleeps, self.pingable, self.battery_powered, self.radio_params)
		return self.pkt


	def __str__(self):
		return "NodeCFG: %s %s %s" % (self.slid, self.name, self.description)

	def json(self):
		d = {
			'slid': self.slid,
			'name': self.name,
			'description': self.description,
			'gps_lat': self.gps_lat,
			'gps_lon': self.gps_lon,
			'altitude': self.altitude,
			'max_silence': self.max_silence,
			'sleeps': self.sleeps,
			'pingable': self.pingable,
			'battery_powered': self.battery_powered,
			'radio_params': self.radio_params
		}
		return json.dumps(d)


# op codes
class SL_OP:
	'''
	control message types: EVEN, 0 bottom bit 
	data message types: ODD, 1 bottom bit
	'''

	DN = 0x30		# data to node, ACK for qos 2
	BN = 0x32		# slid precedes payload, bridge forward to node
	GS = 0x34		# get status, reply with SS message
	TD = 0x36		# transmit a test message via radio
	SR = 0x38		# set radio paramter to x, acknowlegde with AK or NK
	BC = 0x3A		# restart node, no reply
	BR = 0x3C		# reset the radio, acknowlegde with AK or NK

	DS = 0x31		# data to store
	BS = 0x33		# bridge to store
	ON = 0x35		# send status on to store, send on startup
	AK = 0x37		# acknowlegde the last control message
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

class Mqtt:
	"""" run an MQTT connection  """
	def __init__(self, conf, name, loop):
		self.name = name
		self.conf = conf
		self.loop = loop
		self.mq_connected = asyncio.Event(loop=self.loop)
		self.mq_subscribed = asyncio.Event(loop=self.loop)
		self.mq_disconnected = asyncio.Event(loop=self.loop)
		for c in ["clientid", "username", "password"]:
			if not c in conf:
				logging.error("error: %s mqtt %s not specified", self.name, c)
				raise KeyError

		self.mq = aiomqtt.Client(client_id=self.conf["clientid"], loop=self.loop)
		self.mq.loop_start()
		if "cert" in self.conf:
			self.mq.tls_set(self.conf["cert"])
			self.mq.tls_insecure_set(False)
		self.mq.username_pw_set(self.conf["username"],self.conf["password"])
		self.mq.on_connect = self.on_connect
		self.mq.on_subscribe = self.on_subscribe
		self.mq.on_message = self.on_message
		self.mq.on_disconnect = self.on_disconnect
		self.running = True
		self.subscription_list = []


	async def start(self):
		logging.info("%s starting", self.name)
		await self.mq.connect(self.conf["server"], self.conf["port"], 60)
		logger.info("%s mqtt connecting" % self.name)
		await self.wait_connect()

	async def astop(self):
		logger.info("%s done running", self.name)
		await self.mq_disconnected.wait()
		self.mq.loop_stop()


	def stop(self):
		self.running = False
		if self.mq_connected.is_set():
			self.mq.disconnect()
		logging.debug("%s mqtt signaled to disconnect", self.name)


	async def wait_connect(self):
		logging.debug("%s mqtt waiting for connect", self.name)
		await self.mq_connected.wait()
		logging.info("%s mqtt got connected", self.name)
		for topic in self.subscription_list:
			logging.debug("%s on_connect subscribe %s", self.name, topic)
			self.mq.subscribe(topic)
			await self.mq_subscribed.wait()


	def on_subscribe(self, client, userdata, mid, granted_qos):
		self.mq_subscribed.set()

	def on_connect(self, client, userdata, flags, result):
		logging.info("%s mqtt connected %s", self.name, result)
		if result == 0:
			self.mq_connected.set()


	def on_disconnect(self, client, userdata, flags):
		self.mq_connected.clear()
		self.mq_disconnected.set()


	def on_message(self, client, userdata, msg):
		logging.info("%s got %s %s", self.name, msg,topic, json.loads(msg.payload.decode('utf-8')))


#
# TimeLog
#
class TimeLog:
	def __init__(self, maxitems):
		self.maxitems = maxitems
		self.items = collections.OrderedDict()

	def add(self, item):
		while len(self.items) >= self.maxitems:
			self.items.popitem(last=False)
		self.items[time.time()] = item


	def get(self, where, count):
		keys = list(self.items.keys())
		if where in [None, '', 'last']:
			pos = len(keys) 
		else:
			try:
				pos = keys.index(where)
			except:
				pos = 0		# return oldest entry if key not found
				count = abs(count)
		if count < 0:
			start = max(0, (pos + count))
			end = max(0, pos)
		else:
			start = min(pos+1, len(keys))
			end = min(pos+1+count, len(keys))
		print("DBG: pos %s start %s end %s len %s" % (pos, start, end, len(keys)))
		r = {}
		for i in range(start, end):
			r[keys[i]] =  str(self.items[keys[i]])
		return r

if __name__ == '__main__':
	l = TimeLog(10)

	for i in range(20):
		l.add("I-%s" % i)

	r = l.get('', -2)
	print(r)
	r = l.get(list(r.keys())[0], -2)
	print(r)
	r = l.get(list(r.keys())[-1], 2)
	print(r)
	r = l.get(list(r.keys())[-1], 2)
	print(r)


class SteamLinkMqtt(Mqtt):
	def __init__(self, conf, sl_log, loop):
		self.conf = conf
		self.sl_log = sl_log

		for c in ["prefix", "data", "control"]: # , "data", "control"]:
			if not c in conf:
				logging.error("error: %s steamlink_mqtt %s not specified", self.name, c)
				raise KeyError

		self.prefix = conf['prefix']
		self.control_topic_x = "%s/%%s/%s" % (self.prefix, conf['control'])
		self.data_topic_x = "%s/%%s/%s" % (self.prefix, conf['data'])
		self.data_topic = "%s/+/%s" % (self.prefix, conf['data'])

#		super(SteamLinkMqtt, self).__init__(conf, "SteamLink", loop)
		super().__init__(conf, "SteamLink", loop)

		self.subscription_list = [self.data_topic]
		self.mq.message_callback_add(self.data_topic, self.on_data_msg)


	def mk_json_msg(self, msg):
		try:
			payload = msg.payload.decode('utf-8')
			jmsg = {'topic': msg.topic, 'payload': payload }
		except:
			jmsg = {'topic': msg.topic, 'raw': msg.payload }

		logging.debug("steamlink msg %s", str(jmsg))
		return jmsg


	def on_data_msg(self, client, userdata, msg):
		topic_parts = msg.topic.split('/', 2)
		try:
			sl_pkt = SteamLinkPacket(pkt=msg.payload)
		except:
			return

		sl_id = sl_pkt.slid
		if not sl_id in Node.slid_idx:
			logging.warning("SteamLinkMqtt new node with sl_id 0x%0x", sl_id)
			Node(sl_id)
		Node.slid_idx[sl_id].post_data(sl_pkt)
				
	
	def publish(self, firsthop, pkt, qos=0, retain=False, sub="control"):
		s = self.control_topic_x if sub == "control" else self.data_topic_x
		topic = s % firsthop
		logging.info("%s publish %s %s", self.name, topic, pkt)
		self.mq.publish(topic, payload=pkt.pkt, qos=qos, retain=retain)
#		time.sleep(0.1)


class SteamLinkPacket:

	def __init__(self, slnode = None, sl_op = None, rssi = 0, payload = None, pkt = None):
		self.sl_op = None
		self.slid = None
		self.rssi = 0
		self.qos = 0
		self.pkt = None
		self.via = []
		self.payload = None

		if pkt == None:						# construct pkt
			self.slid = slnode.sl_id
			self.sl_op = sl_op
			self.rssi = rssi + 256
			self.payload = payload
#			logging.debug("SteamLinkPaktet payload = %s", payload);
			if self.payload:
				if type(self.payload) == type(b''):
					self.bpayload = self.payload
				else:
					self.bpayload = self.payload.encode('utf8')
			else:
				self.bpayload = b''

			if sl_op == SL_OP.DS:
				sfmt = '<BLB%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid, self.qos, self.bpayload)
			elif sl_op == SL_OP.BS:
				sfmt = '<BLBB%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid, self.rssi, self.qos, self.bpayload)
			elif sl_op == SL_OP.ON:
				sfmt = '<BL%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid, self.bpayload)
			elif sl_op in [SL_OP.AK, SL_OP.NK]:
				sfmt = '<BL' 
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid)
			elif sl_op == SL_OP.TR:
				sfmt = '<BLB%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid, self.rssi, self.bpayload)
			elif sl_op == SL_OP.SS:
				sfmt = '<BL%is' % len(self.bpayload)
				self.sl_op, self.slid, self.bpayload = struct.unpack(sfmt, self.pkt)
				self.payload = self.bpayload.decode('utf8')

			elif sl_op == SL_OP.DN:
				sfmt = '<BLB%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.slid, self.qos, self.rssi, self.bpayload)
			elif sl_op in [SL_OP.GS, SL_OP.BC, SL_OP.BR]:
				sfmt = '<B' 
				self.pkt = struct.pack(sfmt, self.sl_op)
			elif sl_op == SL_OP.TD:
				sfmt = '<B%is' % len(self.bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.bpayload)
			elif sl_op == SL_OP.SR:
				sfmt = '<B%is' % len(bpayload)
				self.pkt = struct.pack(sfmt, self.sl_op, self.bpayload)

			else:
				logging.error("SteamLinkPacket unknown sl_op in pkt %s", self.pkt)

			self.via = [0] #N.B. node_routes[self.slid].via
			if len(self.via) > 0:
				for via in [self.slid]+self.via[::-1][:-1]:
					self.bpayload = self.pkt
					sfmt = '<BL%is' % len(self.bpayload)
					self.pkt = struct.pack(sfmt, SL_OP.BN, via, self.bpayload)
				

		else:								# deconstruct pkt
			self.pkt = pkt
			logging.debug("pkt\n%s", "\n".join(phex(pkt, 4)))

			if pkt[0] == SL_OP.BS:		# un-ecap all
				while pkt[0] == SL_OP.BS:
					sfmt = '<BLBB%is' % (len(pkt) - 7)
					self.sl_op, slid, self.rssi, self.qos, self.bpayload = struct.unpack(sfmt, pkt)
					self.via.append(slid)
					pkt = self.bpayload
					logging.debug("pkg encap BS, len %s\n%s", len(pkt), "\n".join(phex(pkt, 4)))
				self.rssi = self.rssi - 256
#				self.payload = self.bpayload.decode('utf8')

			if pkt[0] == SL_OP.DS:
				sfmt = '<BLB%is' % (len(pkt) - 6)
				self.sl_op, self.slid, self.qos, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			elif pkt[0] == SL_OP.ON:
				sfmt = '<BL%is' % (len(pkt) - 5)
				self.sl_op, self.slid, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			elif pkt[0] in [SL_OP.AK, SL_OP.NK]:
				sfmt = '<BL' 
				self.sl_op, self.slid = struct.unpack(sfmt, pkt)
				self.payload = None
			elif pkt[0] == SL_OP.TR:
				sfmt = '<BLB%is' % (len(pkt) - 6)
				self.sl_op, self.slid, self.rssi, self.bpayload = struct.unpack(sfmt, pkt)
				self.rssi = self.rssi - 256
				try:
					self.payload = self.bpayload.decode('utf8')
				except Exception as e:
					logging.error("cannot decode paket: %s %s", e, pkt);
					raise
			elif pkt[0] == SL_OP.SS:
				sfmt = '<BL%is' % (len(pkt) - 5)
				self.sl_op, self.slid, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')

			elif pkt[0] == SL_OP.DN:
				sfmt = '<BLB%is' % (len(pkt) - 6)
				self.sl_op, self.slid, self.qos, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			elif pkt[0] == SL_OP.BN:
				sfmt = '<BL%is' % (len(pkt) - 5)
				self.sl_op, self.slid, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			elif pkt[0] in [SL_OP.GS, SL_OP.BC, SL_OP.BR]:
				sfmt = '<B' 
				self.sl_op = struct.unpack(sfmt, pkt)
				self.payload = None
			elif pkt[0] == SL_OP.TD:
				sfmt = '<B%is' % (len(pkt) - 1)
				self.sl_op, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			elif pkt[0] == SL_OP.SR:
				sfmt = '<B%is' % (len(pkt) - 1)
				self.sl_op, self.bpayload = struct.unpack(sfmt, pkt)
				self.payload = self.bpayload.decode('utf8')
			else:
				logging.error("SteamLinkPacket unknown sl_op in pkt %s", pkt)

			if (pkt[0] & 0x01) == 1: 	# Data
				self.via.append(self.slid)
				
			

	def __str__(self):
		via = "0x%0x" % self.slid
		if len(self.via) > 0:
			for v in self.via[::-1]: via += "->0x%0x" % v
		s = "SL(op %s, id %s" % (SL_OP.code(self.sl_op), via)
		if self.rssi:
			s += " rssi %s" % (self.rssi)
		if self.payload:
			s += " payload %s" % (self.payload)
		s += ")"
		return s


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
		self.pkt['ts'] = ts


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


class NodeRoutes:
	def __init__(self, dest, via):
		self.dest = dest
		self.via = via


	def __str__(self):
		svia = ""
		for v in self.via:
			svia += "->0x%02x" % v
		return "VIA(0x%0x: %s" % (self.dest, svia)


class Steam:
	console_fields = {
 	 "Name": "self.name",
	 "Description": "self.desc", 
	 }

	def __init__(self, conf):
		self.steam_id = conf.get('id',0) 
		self.name = Steam.get_steam_name(self.steam_id)
		self.desc = conf.get('description', 'descr. for %s' % self.name)
		self.ns = conf.get('namespace', '/steam')
		self.default_room = Room("Steam",self.steam_id)
		self.meshes = {}
		logging.debug("Steam created: %s" % self.name)


	async def start(self):
		await self.console_update(self.default_room)


	def get_steam_name(steam_id):
		return "Steam%i" % steam_id


	def gen_console_data(self):
		r = { 
			'id': self.steam_id,
			'name': self.name,
			'pid': os.getpid(),
			'description': self.desc,
			}
		return {
		  'id': self.steam_id,
		  'display_vals':  r
		}
		return r

	async def console_update(self, room):
		r = self.gen_console_data()
		await a_emit_to_room(r, room)


	async def console_update_full(room):
		await steam.console_update(room)


class Mesh:
	mesh_idx = {}
	console_fields = {
 	 "Name": "self.name",
	 "Description": "self.desc", 
	 "Total Nodes": "len(self.nodes)",
	 "Active Nodes": "len(self.nodes)",
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 }

	def __init__(self, mesh_id):
		self.mesh_id = mesh_id 
		self.name = Mesh.get_mesh_name(mesh_id)
		self.desc = "descr. for %s" % self.name
		self.packets_sent = 0
		self.packets_received = 0
		self.default_room = Room("Mesh",self.name)
		self.nodes = {}
		Mesh.mesh_idx[self.name] = self
		logging.debug("Mesh created: %s" % self.name)
		self.console_update(self.default_room)


	def get_mesh_name(mesh_id):
		return "Mesh%06x" % mesh_id

	def __del__(self):
		del Mesh.mesh_idx[self.name] 
		self.console_update(self.default_room)


	def add_node(self, node):
		self.nodes[node.sl_id] = node
		self.console_update(self.default_room)


	def del_node(self, node):
		del self.nodes[node.sl_id] 
		self.console_update(self.default_room)


	def gen_console_data(self):
		r = {}
		for label in Mesh.console_fields:
			source = Mesh.console_fields[label]
			try:
				v = eval(source)
			except:
				v = "*UNK*"
			r[label] = v
		return {
		  'id': self.name,
		  'display_vals':  r
		}

	def console_update(self, room):
		r = self.gen_console_data()
		emit_to_room(r, room)


	def console_update_full(room):
		logging.debug("Mesh console_update_full for %s", room)
		if len(Mesh.mesh_idx) == 0:
			return
		if room.key == '*':		# all meshes
			print("mesh index:", Mesh.mesh_idx.keys())
			for m in list(Mesh.mesh_idx.keys()):
				Mesh.mesh_idx[m].console_update(room)
		else:
			if not room.key in Mesh.mesh_idx:
				logging.error("console did not find %s" % room.key)
				return
			if room.detail:
				for node in Mesh.mesh_idx[room.key].nodes.keys():
					Mesh.mesh_idx[room.key].nodes[node].console_update(room)
			else:
				Mesh.mesh_idx[room.key].console_update(room)

#
# Node
#
class Node:
	slid_idx = {}
	name_idx = {}
	sl_broker = {}
	console_fields = {
 	 "Name": "self.nodecfg.name",
	 "Description": "self.nodecfg.desc", 
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 "SL ID": "self.sl_id", 
	}
	""" a node in the test set """
	def __init__(self, sl_id, nodecfg = None):
		self.sl_id = sl_id
		self.nodecfg = nodecfg
		self.name = self.mkname()
		self.response_q = queue.Queue(maxsize=1)

		self.packets_sent = 0
		self.packets_received = 0
		self.default_room = Room("Node",self.name)
		self.state = "DOWN"	
		self.status = []
		self.tr = {}		# dict of sending nodes, each holds a list of (pktno, rssi)

		self.packet_log = TimeLog(MAX_NODE_LOG_LEN)

		assert not self.sl_id in Node.slid_idx, "on add: sl_id %s already in index" % sl_id
		Node.slid_idx[self.sl_id] = self
		Node.name_idx[self.name] = self
		logging.debug("Node created: %s" % self.name)

		mesh_id = self.mesh_id()
		self.mesh_name = Mesh.get_mesh_name(mesh_id)
		if not self.mesh_name in Mesh.mesh_idx:
			Mesh(mesh_id)
		Mesh.mesh_idx[self.mesh_name].add_node(self)
		self.console_update(self.default_room)

	def __del__(self):
		assert self.sl_id in Node.slid_idx, "on del: sl_id %s already not index" % self.sl_id
		assert self.name in Node.name_idx, "on del: name %s already not index" % self.name
		mesh_id = self.mesh_id()
		Mesh.mesh_idx[self.mesh_name].del_node(self)
		del Node.name_idx[self.name]
		del Node.slid_idx[self.sl_id]
		self.console_update(self.default_room)

	def mkname(self):
		if self.nodecfg:
			return self.nodecfg.name
		return "Node%08x" % self.sl_id


	def mesh_id(self):
		return (self.sl_id >> 8)


	def get_firsthop(self):
		route_via = [] # N.B. node_routes[self.sl_id].via
		if len(route_via) == 0:
			firsthop = self.sl_id
		else:
			firsthop = route_via[0]
		return firsthop


	def set_state(self, new_state):
		if self.state != new_state:
			self.state = new_state
			logging.info("node %s state %s", self.sl_id, self.state)
#			sl_log.log_state(self.sl_id, "ONLINE" if self.state == "UP" else "offline")


	def is_up(self):
		return self.state == "UP"


	def publish_pkt(self, sl_pkt, sub="control"):
		self.log_pkt(sl_pkt)
		self.packets_sent += 1
		self.console_update(self.default_room)
		Node.sl_broker.publish(self.get_firsthop(), sl_pkt, sub=sub)


	def send_boot_cold(self):
		sl_pkt = SteamLinkPacket(slnode=self, sl_op=SL_OP.BC)
		self.publish_pkt(sl_pkt)
		return 


	def send_get_status(self):
		sl_pkt = SteamLinkPacket(slnode=self, sl_op=SL_OP.GS)
		self.publish_pkt(sl_pkt)
#		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		return 


	def send_set_radio_param(self, radio):
		if self.state != "UP": return SL_OP.NC
		lorainit = struct.pack('<BLB', 0, 0, radio)
		logging.debug("send_set_radio_param: len %s, pkt %s", len(lorainit), lorainit)
		sl_pkt = SteamLinkPacket(slnode=self, sl_op=SL_OP.SR, payload=lorainit)
		self.publish_pkt(sl_pkt)

		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		return rc


	def send_testpacket(self, pkt):
		if self.state != "UP": return SL_OP.NC
		sl_pkt = SteamLinkPacket(slnode=self, sl_op=SL_OP.TD, payload=pkt)
		self.publish_pkt(sl_pkt)
		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		logging.debug("send_packet %s got %s", sl_pkt, SL_OP.code(rc))
		return rc


	def __repr__(self):
		return "Node %s" % (self.sl_id)


	def log_pkt(self, sl_pkt):
		self.packet_log.add(sl_pkt)


	def post_data(self, sl_pkt):
		""" handle incoming messages on the ../data topic """
		self.log_pkt(sl_pkt)
		self.packets_received += 1
		self.console_update(self.default_room)

		logging.info("post_data %s", sl_pkt)

		# any pkt from node indicates it's up
		self.set_state('UP')

		sl_op = sl_pkt.sl_op

		if sl_op == SL_OP.ON:
			logging.debug('post_data: slid 0x%0x ONLINE', self.sl_id)
			self.nodecfg = SL_NodeCfgStruct(pkg=sl_pkt.bpayload)

		elif sl_op == SL_OP.DS:
			logging.debug('post_data: slid 0x%0x status %s', self.sl_id,sl_pkt.payload)
			self.status = sl_pkt.payload.split(',')

		elif sl_op == SL_OP.SS:
			logging.debug('post_data: slid 0x%0x status %s', self.sl_id,sl_pkt.payload)
			a

		elif sl_op in [SL_OP.AK, SL_OP.NK]:
			logging.debug('post_data: slid 0x%0x answer %s', self.sl_id, SL_OP.code(sl_op))
			try:
				self.response_q.put(sl_op, block=False)
			except queue.Full:
				logging.warning('post_data: node %s queue, dropping: %s', self.sl_id, sl_pkt)
		elif sl_op == SL_OP.TR:
			logging.debug('post_data: node %s test msg', sl_pkt.payload)

			try:
				test_pkt = TestPkt(pkt=sl_pkt.payload)
			except ValueError as e:
				logging.warning("post_incoming: cannot convert %s to pkt", sl_pkt.payload)
				return
			
			test_pkt.set_receiver_slid(sl_pkt.via)
			test_pkt.set_rssi(sl_pkt.rssi)
			if not test_pkt.pkt['slid'] in self.tr:
				self.tr[test_pkt.pkt['slid']] = []
			self.tr[test_pkt.pkt['slid']].append((test_pkt.pkt['pktno'], test_pkt.pkt['rssi']))
#			sl_log.post_incoming(test_pkt)
			
	
	def get_response(self, timeout):
		try:
			data = self.response_q.get(block=True, timeout=timeout)
		except queue.Empty:
			data = SL_OP.NC
		return data
		

	def gen_console_data(self):
		r = {}
		for label in Node.console_fields:
			source = Node.console_fields[label]
			try:
				v = eval(source)
			except:
				v = "*UNK*"
			r[label] = v

		return {
		  'id': self.name,
		  'display_vals':  r
		}


	def console_update_full(room):
		if len(Node.name_idx) == 0:
			return
		if room.key == '*':		# all nodes
			print("node index:", Node.name_idx.keys())
			for m in list(Node.name_idx.keys()):
				Node.name_idx[m].console_update(room)
		else:
			if not room.key in Node.name_idx:
				logging.error("console did not find %s" % room.key)
				return
			if room.detail:		# FIX when item paging
				Node.name_idx[room.key].console_pkt_log(room, '', -20)
			else:
				Node.name_idx[room.key].console_update(room)


	def console_update(self, room):
		r = self.gen_console_data()
		emit_to_room(r, room)


	def console_tail(self, room):
		v = self.packet_log.get('',-1)
		r = {
		  'id': key,
		  'display_vals':  { 'data': v }
		}
		emit_to_room(r, room)


	def console_pkt_log(self, room, key, count):
		v = self.packet_log.get(key, count)
		r = {
		  'id': key,
		  'display_vals':  v 
		}
		emit_to_room(r, room)


# 
# LogData
#
class LogData:
	""" Handle incoming pkts on the ../data topic """
	def __init__(self, conf):
		self.conf = conf
		self.logfile = open(conf["file"],"a+")
		self.pkt_inq = queue.Queue()
		self.nodes_online = 0
		

	def log_state(self, sl_id, new_state):
		logging.debug("logdata node 0x%0x %s", sl_id, new_state)
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
			except queue.Empty:
				test_pkt = None
			logging.debug("wait_pkt_number pkt %s", test_pkt)
			waited = time.time() - now
			if test_pkt and test_pkt.pkt['pktno'] == pktnumber and packets_seen == num_packets:
				return pktnumber
			if waited >= lwait or test_pkt.pkt['pktno'] > pktnumber:	# our pkt will never arrive
				return None
			lwait -= waited

async def aemit(r, room):
	logging.debug("ROOM %s EMIT %s" % (room, r))
	try:
		await sio.emit('data_full', r, namespace=steam.ns, room=room)
	except Exception as e:
		logging.warn("emit %s exception: %s", room, e)


async def a_emit_to_room(r, room):
	logging.debug("emit_to_room %s: %s",room,r)
	sroom = str(room)
	if room.no_key() != sroom:
		await aemit(r, room.no_key())
	if room.is_header():
		r['header'] = True
	await aemit(r, sroom)


def emit(r, room):
	logging.debug("ROOM %s EMIT %s", room, r)
	try:
		asyncio.run_coroutine_threadsafe(sio.emit('data_full', r, namespace=steam.ns, room=room), aioloop)
	except Exception as e:
		logging.warn("emit %s exception: %s", room, e)


def emit_to_room(r, room):
	logging.debug("emit_to_room %s: %s",room,r)
	sroom = str(room)
	if room.no_key() != sroom:
		emit(r, room.no_key())
	if room.is_header():
		r['header'] = True
	emit(r, sroom)


FORMAT = '%(asctime)-15s: %(message)s'
logging.basicConfig(format=FORMAT)
logger = logging.getLogger()
