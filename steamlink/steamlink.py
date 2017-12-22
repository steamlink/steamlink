#!/usr/bin/env python3

# Control program for a Stealink network

import sys
import os
import struct
import collections
import queue
import json
import time
import yaml
import argparse
import random

import asyncio
import socketio
from aiohttp import web

from steamlink.timelog import TimeLog

import logging
logger = logging.getLogger(__name__)


ItemTypes = ['Steam', 'Mesh', 'Node', 'Pkt']


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
#
# Room
#
class Room:
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
		assert self.lvl in ItemTypes , "room key invalid: %s" % sroom


	def is_item_room(self):
		return self.detail != None


	def is_header(self):
		if self.detail == '*' or self.key == '*':
			return False
		return True


	def no_key(self):
		return "%s_*" % (self.lvl)
		

	def __str__(self):
		if not self.detail is None:
			return "%s_%s_%s" % (self.lvl, self.key, self.detail)
		return "%s_%s" % (self.lvl, self.key)
		

		
#
# SL_CodeCfgStruct 
#
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
		if pkt is None:	 # construct
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


#
# SL_OP op codes
#
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
#
# Registry
#
class Registry:
	def __init__(self):
		self.name_idx = {}
		for lvl in ItemTypes:
			self.name_idx[lvl] = {}
		self.id_idx = {}
		for lvl in ItemTypes:
			self.id_idx[lvl] = {}

	def register(self, item):
		logger.debug("Registry: register %s", item)
#		assert not item.name in self.name_idx[item.itype], "Name already in Index"
		logger.debug("Registry: 0 registered %s", item)
		self.name_idx[item.itype][item.name] = item
#		assert not item.key in self.id_idx[item.itype], "Id already in Index"
		self.id_idx[item.itype][item.key] = item
		logger.debug("Registry: registered %s", item)

	def unregister(self, item):
		logger.debug("Registry: unregister %s", item)
#		assert item.name in self.name_idx[item.itype], "Name not in Index"
		del self.name_idx[item.itype][item.name] 
#		assert item.key in self.id_idx[item.itype], "Id not in Index"
		del self.id_idx[item.itype][item.key]


	def get_all(self, itype):
		r = []
		for key in self.id_idx[itype]:
			r.append(self.id_idx[itype][key])
		return r


	def find_by_name(self, itype, name):
#		t = self.name_idx.get(itype, None)
#		if t is None:
#			return None
		t = self.name_idx[itype].get(name, None)
		return t

	def find_by_id(self, itype, Id):
#		t = self.id_idx.get(itype, None)
#		if t is None:
#			return None
		t = self.id_idx[itype].get(Id, None)
		logger.debug("find_by_id %s %s = %s", itype, Id, str(t))
		return t


# create registry
registry = Registry()

#
# Item
#
class Item:
	def __init__(self):
		logger.debug("Item: creating %s", self)
		assert self.itype in ItemTypes, "Item.__init__: %s not an ItemType" % self.itype
		self.children = {}		# keyed by _key of decendent
		registry.register(self)
		logger.debug("Item: afer register %s", self)
		p = self.get_parent()
		if p:
			logger.debug("Item: add  %s to %s", self, p)
			p.add_child(self)
		logger.debug("Item: created %s", self)


	def __del__(self):
		logger.debug("Item: delete %s", self)
		p = self.get_parent()
		if p:
			p.del_child(self)
		registry.unregister(self)


	def get_parent_type(self):
		i = ItemTypes.index(self.itype)
		if i == 0:
			return None
		return ItemTypes[i-1]


	def get_parent(self):
		ptype = self.get_parent_type()
		if ptype is None:
			p = None
		else:
			p =  registry.find_by_id(ptype, 0)
		logger.debug("Item: get_parent  %s): %s", self, str(p))
		return p


	def add_child(self, item):
		logger.debug("Item: child  %s added to %s", item.key, self)
		self.children[item.key] = item
#?		self.console_update(self.default_room)


	def del_child(self, item):
		logger.debug("Item: child  %s delete from %s", item.key, self)
		del self.children[item.key] 
#?		self.console_update(self.default_room)


	def schedule_item_update(self):
		logger.debug("Item: schedule_item_update %s", self)
		# XXX


	def gen_console_data(self):
		cs = []
		for c in self.children:
			cs.append(str(self.children[c]))
		r = {
		  'name': self.name,
		  'type': self.itype,
		  'id': self.key,
		  'children': str(cs),
		}
		return self.key, r


#	def gen_datafull_emit(self):
#		r = self.gen_console_data()
#		return {
#		  'id': self.key,
#		  'type': self.itype, 
#		  'display_vals':  r
#		}


	def get_room_list(self):
		return ['%s_%s' % (self.type, self.key)]


	def update(self, item):
		all_room = "%s_*" % (self.itype)
		head_room = "%s_%s" % (self.itype, self.key)
		item_room = "%s_%s_*" % (self.itype, self.key)

		rooms = [all_room, head_room, item_room]


	def __str__(self):
		return "%s %s(%s)" % (self.itype, self.name, self.key)

#
# Steam
#
class Steam(Item):
	root = None
	console_fields = {
 	 "Name": "self.name",
	 "Description": "self.desc", 
	 }
	
	def __init__(self, conf, broker,  sio):
		self.key = conf.get('id', 0) 
		name = self.mkname()
		self.name = conf.get('name', name)
		self.itype = 'Steam'
		self.sio = sio
		self.desc = conf.get('description', 'Description for %s' % name)
		self.ns = conf.get('namespace', '/sl')
		self.default_room = Room("Steam", self.key)
		self.sl_broker = broker
		super().__init__()


	async def start(self):
#		await self.console_update(self.default_room)
		pass

	async def stop(self):
		logger.debug("stopping steam")

	def mkname(self):
		return "Steam%i" % self.key


#
# Mesh
#
class Mesh(Item):
	console_fields = {
 	 "Name": "self.name",
	 "Description": "self.desc", 
	 "Total Nodes": "len(self.children)",
	 "Active Nodes": "len(self.children)",
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 }

	def __init__(self, mesh_id):
		logger.debug("Mesh creating: %s", mesh_id)
		self.key = mesh_id 
		self.name = self.mkname()
		self.itype = 'Mesh'
		self.steam = registry.find_by_id('Steam', 0)
		self.desc = "Description for %s" % self.name
		self.packets_sent = 0
		self.packets_received = 0
		self.default_room = Room("Mesh", self.name)
		super().__init__()
		logger.debug("Mesh created: %s", self.name)

#?		self.console_update(self.default_room)


	def mkname(self):
		return "Mesh%06x" % self.key


	def get_room_list(self):
		return [
			"%_*" % self.type,
			'%s_%s' % (self.type, self.key),
			'%s_%s' % (self.type, self.key),
			"%s_%s_*" % ("Node", self.key),
			] 


	def gen_console_data(self):
		r = {}
		for label in Mesh.console_fields:
			source = Mesh.console_fields[label]
			try:
				v = eval(source)
			except:
				v = "*UNK*"
			r[label] = v
		return self.key, r

#
# Node
#
class Node(Item):
	console_fields = {
 	 "Name": "self.nodecfg.name",
	 "Description": "self.nodecfg.desc", 
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 "SL ID": "self.key", 
	}
	""" a node in the test set """
	def __init__(self, sl_id, nodecfg = None, steam = None):
		logger.debug("Node createing : %s" % sl_id)
		self.nodecfg = nodecfg
		self.key = sl_id
		self.name = self.mkname()
		self.itype = 'Node'
		self.steam = registry.find_by_id('Steam', 0)
		self.response_q = queue.Queue(maxsize=1)

		self.packets_sent = 0
		self.packets_received = 0
		self.default_room = Room("Node", self.name)
		self.state = "DOWN"	
		self.status = []
		self.tr = {}		# dict of sending nodes, each holds a list of (pktno, rssi)
		self.packet_log = TimeLog(MAX_NODE_LOG_LEN)

		mesh = registry.find_by_id('Mesh', self.mesh_id())
		if mesh is None:		# Auto-create Mesh
			mesh = Mesh(mesh_id, steam)
			logger.debug("Node __init__: mesh is %s" % mesh)

		super().__init__()

		logger.debug("Node created: %s" % self.name)

#?		self.console_update(self.default_room)


	def mkname(self):
		if self.nodecfg:
			return self.nodecfg.name
		return "Node%08x" % self.key


	def mesh_id(self):
		return (self.key >> 8)


	def get_parent(self):
		ptype = self.get_parent_type()
		if ptype is None:
			p = None
		else:
			p = registry.find_by_id(ptype, self.mesh_id())
		logger.debug("Node: get_parent  %s: %s", self, p)
		return p


	def get_firsthop(self):
		route_via = [] # N.B. node_routes[self.key].via
		if len(route_via) == 0:
			firsthop = self.key
		else:
			firsthop = route_via[0]
		return firsthop


	def set_state(self, new_state):
		if self.state != new_state:
			self.state = new_state
			logger.info("node %s state %s", self.key, self.state)
#			sl_log.log_state(self.key, "ONLINE" if self.state == "UP" else "offline")


	def is_up(self):
		return self.state == "UP"


	def publish_pkt(self, sl_pkt, sub="control"):
		self.log_pkt(sl_pkt)
		self.packets_sent += 1
#?		self.console_update(self.default_room)
		self.steam.sl_broker.publish(self.get_firsthop(), sl_pkt, sub=sub)


	def send_boot_cold(self):
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.BC)
		self.publish_pkt(sl_pkt)
		return 


	def send_get_status(self):
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.GS)
		self.publish_pkt(sl_pkt)
#		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		return 


	def send_set_radio_param(self, radio):
		if self.state != "UP": return SL_OP.NC
		lorainit = struct.pack('<BLB', 0, 0, radio)
		logger.debug("send_set_radio_param: len %s, pkt %s", len(lorainit), lorainit)
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.SR, payload=lorainit)
		self.publish_pkt(sl_pkt)

		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		return rc


	def send_testpacket(self, pkt):
		if self.state != "UP": return SL_OP.NC
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.TD, payload=pkt)
		self.publish_pkt(sl_pkt)
		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		logger.debug("send_packet %s got %s", sl_pkt, SL_OP.code(rc))
		return rc


	def __repr__(self):
		return "Node %s" % (self.key)


	def log_pkt(self, sl_pkt):
		self.packet_log.add(sl_pkt)


	def post_data(self, sl_pkt):
		""" handle incoming messages on the ../data topic """
		self.log_pkt(sl_pkt)
		self.packets_received += 1
#?		self.console_update(self.default_room)

		logger.info("post_data %s", sl_pkt)

		# any pkt from node indicates it's up
		self.set_state('UP')

		sl_op = sl_pkt.sl_op

		if sl_op == SL_OP.ON:
			logger.debug('post_data: slid 0x%0x ONLINE', self.key)
			self.nodecfg = SL_NodeCfgStruct(pkg=sl_pkt.bpayload)

		elif sl_op == SL_OP.DS:
			logger.debug('post_data: slid 0x%0x status %s', self.key,sl_pkt.payload)
			self.status = sl_pkt.payload.split(',')

		elif sl_op == SL_OP.SS:
			logger.debug('post_data: slid 0x%0x status %s', self.key,sl_pkt.payload)
			a

		elif sl_op in [SL_OP.AK, SL_OP.NK]:
			logger.debug('post_data: slid 0x%0x answer %s', self.key, SL_OP.code(sl_op))
			try:
				self.response_q.put(sl_op, block=False)
			except queue.Full:
				logger.warning('post_data: node %s queue, dropping: %s', self.key, sl_pkt)
		elif sl_op == SL_OP.TR:
			logger.debug('post_data: node %s test msg', sl_pkt.payload)

			try:
				test_pkt = TestPkt(pkt=sl_pkt.payload)
			except ValueError as e:
				logger.warning("post_incoming: cannot convert %s to pkt", sl_pkt.payload)
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
		

	def get_room_list(self):
		return [
			"%s_*" % self.type,
			"%s_%s_*" % (self.type, self.key),
			'%s_%s' % (self.type, self.key)
			] 


	def gen_console_data(self):
		r = {}
		for label in Node.console_fields:
			source = Node.console_fields[label]
			try:
				v = eval(source)
			except:
				v = "*UNK*"
			r[label] = v
		return self.key, r


#	def console_tail(self, room):
#		v = self.packet_log.get('',-1)
#		r = {
#		  'id': key,
#		  'type': 'pkt',
#		  'display_vals':  { 'data': v }
#		}
#		emit_to_room(r, room)


	async def console_pkt_log(self, room, key, count):
		v = self.packet_log.get(key, count)
		r = {
		  'id': key,
		  'type': 'pkt',
		  'display_vals':  v 
		}
		a_emit_to_room(r, room, self.steam)


#
# Packet
#
class Packet(Item):
	Number = 0
	def __init__(self, slnode = None, sl_op = None, rssi = 0, payload = None, pkt = None):
		self.key = Packet.Number
		self.name = "pktno-%s" % self.key
		Packet.Number += 1
		self.itype = 'Pkt'

		self.sl_op = None
		self.slid = None
		self.rssi = 0
		self.qos = 0
		self.pkt = None
		self.via = []
		self.payload = None

		super().__init__()

		if pkt is None:						# construct pkt
			self.slid = slnode.sl_id
			self.sl_op = sl_op
			self.rssi = rssi + 256
			self.payload = payload
#			logger.debug("SteamLinkPaktet payload = %s", payload);
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
				logger.error("Packet unknown sl_op in pkt %s", self.pkt)

			self.via = [0] #N.B. node_routes[self.slid].via
			if len(self.via) > 0:
				for via in [self.slid]+self.via[::-1][:-1]:
					self.bpayload = self.pkt
					sfmt = '<BL%is' % len(self.bpayload)
					self.pkt = struct.pack(sfmt, SL_OP.BN, via, self.bpayload)
				

		else:								# deconstruct pkt
			self.pkt = pkt
			logger.debug("pkt\n%s", "\n".join(phex(pkt, 4)))

			if pkt[0] == SL_OP.BS:		# un-ecap all
				while pkt[0] == SL_OP.BS:
					sfmt = '<BLBB%is' % (len(pkt) - 7)
					self.sl_op, slid, self.rssi, self.qos, self.bpayload = struct.unpack(sfmt, pkt)
					self.via.append(slid)
					pkt = self.bpayload
					logger.debug("pkg encap BS, len %s\n%s", len(pkt), "\n".join(phex(pkt, 4)))
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
					logger.error("cannot decode paket: %s %s", e, pkt);
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
				logger.error("Packet unknown sl_op in pkt %s", pkt)

			if (pkt[0] & 0x01) == 1: 	# Data
				self.via.append(self.slid)
				


	def o__str__(self):
		if self.slid is None:
			via = "-%s-" % self.key
		else:
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


	def get_room_list(self):
		return [
			"%s_*" % self.type,
			"%s_%s_*" % (self.type, self.key),
			'%s_%s' % (self.type, self.key)
			] 

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
		return "VIA(0x%0x: %s" % (self.dest, svia)


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
		logger.debug("logdata node 0x%0x %s", sl_id, new_state)
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
			logger.debug("wait_pkt_number pkt %s", test_pkt)
			waited = time.time() - now
			if test_pkt and test_pkt.pkt['pktno'] == pktnumber and packets_seen == num_packets:
				return pktnumber
			if waited >= lwait or test_pkt.pkt['pktno'] > pktnumber:	# our pkt will never arrive
				return None
			lwait -= waited

async def aemit(r, room, steam):
	logger.debug("ROOM %s EMIT %s" % (room, r))
	try:
		await steam.sio.emit('data_full', r, namespace=steam.ns, room=room)
	except Exception as e:
		logger.warn("emit %s exception: %s", room, e)


async def a_emit_to_room(r, room, steam):
	logger.debug("emit_to_room %s: %s",room,r)
	sroom = str(room)
	if room.no_key() != sroom:
		await aemit(r, room.no_key(), steam)
	if room.is_header():
		r['header'] = True
	await aemit(r, sroom, steam)


#def emit(r, room):
#	logger.debug("ROOM %s EMIT %s", room, r)
#	try:
#		asyncio.run_coroutine_threadsafe(sio.emit('data_full', r, namespace=steam.ns, room=room), aioloop)
#	except Exception as e:
#		logger.warn("emit %s exception: %s", room, e)
#
#
#def emit_to_room(r, room):
#	logger.debug("emit_to_room %s: %s",room,r)
#	sroom = str(room)
#	if room.no_key() != sroom:
#		emit(r, room.no_key())
#	if room.is_header():
#		r['header'] = True
#	emit(r, sroom)


#FORMAT = '%(asctime)-15s: %(message)s'
#logging.basicConfig(format=FORMAT)
#logger = logging.getLogger()
