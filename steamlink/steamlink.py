#!/usr/bin/env python3

# python library Stealink network

import struct
import queue
import json
import time
import asyncio

from steamlink.timelog import TimeLog

import logging
logger = logging.getLogger(__name__)


ItemTypes = ['Steam', 'Mesh', 'Node', 'Pkt', 'Room']

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


# Globals, initialized by attach
_LOOP = None
_WEBAPP = None
_BROKER = None

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
#			assert struct.calcsize(SL_NodeCfgStruct.sfmt) == len(pkt)
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
		if logging.DBG > 2: logger.debug("Registry: register %s", item)
#		assert not item.name in self.name_idx[item.itype], "Name already in Index"
		if logging.DBG > 2: logger.debug("Registry: 0 registered %s", item)
		self.name_idx[item.itype][item.name] = item
#		assert not item.key in self.id_idx[item.itype], "Id already in Index"
		self.id_idx[item.itype][item.key] = item
		if logging.DBG > 2: logger.debug("Registry: registered %s", item)

	def unregister(self, item):
		if logging.DBG > 2: logger.debug("Registry: unregister %s", item)
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
		if logging.DBG > 2: logger.debug("find_by_id %s %s = %s", itype, Id, str(t))
		return t


# create registry
registry = Registry()

#
# BaseItem
#
class BaseItem:
	def __init__(self, itype, key, name = None, key_in_parent = 0):
		self.itype = itype
		self.key = key
		if name is None:
			self.name = self.mkname()
		else:
			self.name = name
		self.parent = None
		self.children = {}
		logger.debug("BaseItem: created %s", self.__str__())
		registry.register(self)
		self.pp = False


	def __del__(self):
		if registry.find_by_id(self.itype, self.key):
			registry.unregister(self)
			logger.debug("BaseItem: deleted %s", self)


	def mkname(self):
		return "%s:%s" % (self.itype, self.key)


	def __str__(self):
		return "%s %s(%s)" % (self.itype, self.name, self.key)


	def pretty(self):
		import pprint

		if self.pp:
			return "XXX"
		self.pp = True
		res = pprint.pformat(self.__dict__, indent=2, width=80)
		self.pp = False
		return res


#
# RoomItem
#
class RoomItem:
	def __init__(self, room, item):
		self.room = room
		self.item = item
		self.item_key = "%s_%s" % (item.itype, item.key)
		self.last_update = 0		# room's last update time stamp
		self.future_update = 0		# room's next update time stamp


	def update(self):
		self.last_update = _LOOP.time()
		self.item.console_update([self.room])

#
# Room
#
class Room(BaseItem):
	def __init__(self, lvl = None, rkey = None, detail = None, sroom = None):

		if sroom:
			l = sroom.split('_')
			if  len(l) < 2 or len(l) > 3:
				logger.error("Room: sroom string invalid: %s" % sroom)
				l.append("*")	# XXX
			self.lvl = l[0]
			self.rkey = l[1]
			self.detail = None if len(l) < 3 else l[2]
			self.sroom = sroom
		else:
			self.lvl = lvl
			self.rkey = rkey
			self.detail = detail
			self.sroom = self.__str__()
		super().__init__('Room', sroom)

		self.last_update = 0		# room's last update time stamp
		self.future_update = 0		# room's next update time stamp
		self.members = {}			# fake dict: web session in room
		self.items = []				# item keys in room


	def is_item_room(self):
		return self.detail != None


	def is_header(self):
		if self.detail == '*' or self.rkey == '*':
			return False
		return True


	def no_key(self):
		return "%s_*" % (self.lvl)


	def full_key(self):
		return self.sroom


	def add_member(self, mid):
		if mid in self.members:
			logger.error("room add_member: id %s already a member in room", mid, self)
		self.members[mid] = mid


	def del_member(self, mid):
		if not mid in self.members:
			logger.error("room del_member: id %s not a member in room %s", mid, self)
		else:
			del self.members[mid] 

#	def __str__(self):
#		if not self.detail is None:
#			return "%s_%s_%s" % (self.lvl, self.rkey, self.detail)
#		return "%s_%s" % (self.lvl, self.rkey)



#
# Item
#
class Item(BaseItem):
	def __init__(self, itype, key, name = None, key_in_parent = 0):
		super().__init__(itype, key, name)

		self.parent = self.get_parent(key_in_parent)
		if self.parent:
			self.parent.add_child(self)

		self.my_rooms = []
		for r in self.get_room_list():
			logger.debug("Item: room to add %s", r)
			room = registry.find_by_id('Room', r)
			if room is None:
				room = Room(sroom = r)
			self.my_rooms.append(room)
			room.items.append(self)		#XXX del

		self.schedule_update()


	def __del__(self):
		if registry.find_by_id(self.itype, self.key):
			super().__del__()
			if self.parent:
				self.parent.del_child(self)
			self.schedule_update()	#XXX update type: del?


	def attach(self, app, broker):
		global _ROOT, _WEBAPP, _LOOP, _BROKER
		if _WEBAPP is not None:
			logger.error("Item %s: attach already done", self.key)
			return
		_ROOT = self
		_WEBAPP = app
		_BROKER = broker
		_LOOP = asyncio.get_event_loop()
		logger.debug("Item: attached webapp '%s'", _WEBAPP.name)


	def get_parent_type(self):
		i = ItemTypes.index(self.itype)
		if i == 0:
			return None
		return ItemTypes[i-1]


	def get_parent(self, key_in_parent = 0):
		ptype = self.get_parent_type()
		if ptype is None:
			p = None
		else:
			p =  registry.find_by_id(ptype, key_in_parent)
		if logging.DBG > 1: logger.debug("Item: get_parent  %s): %s", self, str(p))
		return p


	def add_child(self, item):
		logger.debug("Item: child  %s added to %s", item.key, self)
		self.children[item.key] = item
		self.schedule_update()


	def del_child(self, item):
		logger.debug("Item: child  %s delete from %s", item.key, self)
		del self.children[item.key]
		self.schedule_update()


	def schedule_update(self, rooms = None):
		if not _WEBAPP:
			logger.warning("Item %s: update before _WEBAPP", self.name)
			return
		if rooms is None:
			rooms = self.my_rooms
		logger.debug("Item %s: schedule_update for %s", self.name, rooms)
		_WEBAPP.schedule_update(self, rooms)


	def console_update(self, rooms = None):
		res = []
		data_id, data_to_emit = self.gen_console_data()
		pack =  {
		  'id': data_id,
		  'type': self.itype,
		  'display_vals':  data_to_emit,
		}
		for room in rooms:
			if room.is_header():
				pack['header'] = True
			else:
				if 'header' in pack: del pack['header']
			res.append((room.sroom, pack))
		return res


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


	def get_room_list(self):
		rooms = []
		rooms.append( "%s_*" % (self.itype))
		rooms.append( "%s_%s" % (self.itype, self.key))
		if self.parent:
			rooms.append( "%s_%s_*" % (self.parent.itype, self.parent.key))
		return rooms


#
# Steam
#
class Steam(Item):
	console_fields = {
 	 "Name": "self.name",
 	 "Key": "self.key",
#	 "Description": "self.desc",
	 "Time": "time.asctime()",
	 }


	def __init__(self, conf):
		self.desc = conf['description']
		super().__init__('Steam', conf['id'])


	def gen_console_data(self):
		r = {}
		for label in Steam.console_fields:
			source = Steam.console_fields[label]
			try:
				v = eval(source)
			except:
				v = "*UNK*"
			r[label] = v
		return self.key, r


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
#		self.steam = registry.find_by_id('Steam', 0)
		self.packets_sent = 0
		self.packets_received = 0
		super().__init__('Mesh', mesh_id)
		self.desc = "Description for %s" % self.name
		logger.debug("Mesh created: %s", self.name)


	def mkname(self):
		return "Mesh%06x" % self.key



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
	def __init__(self, sl_id, nodecfg = None):
		logger.debug("Node createing : %s" % sl_id)
		if nodecfg is None:
			self.nodecfg = SL_NodeCfgStruct(sl_id, "Node%08x" % sl_id)
		else:
			self.nodecfg = nodecfg
			self.name = nodecfg.name
#		self.steam = registry.find_by_id('Steam', 0)
		self.response_q = queue.Queue(maxsize=1)

		self.packets_sent = 0
		self.packets_received = 0
		self.state = "DOWN"
		self.status = []
		self.tr = {}		# dict of sending nodes, each holds a list of (pktno, rssi)
		self.packet_log = TimeLog(MAX_NODE_LOG_LEN)


		super().__init__('Node', sl_id, None, key_in_parent=self.mesh_id(sl_id))

		self.mesh = registry.find_by_id('Mesh', self.mesh_id())
		if self.mesh is None:		# Auto-create Mesh
			self.mesh = Mesh(mesh_id)
			logger.debug("Node __init__: mesh is %s" % mesh)

		logger.debug("Node created: %s" % self.name)


	def mkname(self):
		if self.nodecfg:
			return self.nodecfg.name
		return "Node%08x" % self.key


	def mesh_id(self, key = None):
		if key is None:
			key = self.key
		return (key >> 8)


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
			self.schedule_update()



	def is_up(self):
		return self.state == "UP"


	def publish_pkt(self, sl_pkt, sub="control"):
		self.log_pkt(sl_pkt)
		self.packets_sent += 1
		self.schedule_update()
		self.mesh.packets_sent += 1
		_BROKER.publish(self.get_firsthop(), sl_pkt, sub=sub)
		self.mesh.schedule_update()


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


	def log_pkt(self, sl_pkt):
		self.packet_log.add(sl_pkt)


	def post_data(self, sl_pkt):
		""" handle incoming messages on the ../data topic """
		self.log_pkt(sl_pkt)
		self.packets_received += 1
		self.schedule_update()
		self.mesh.packets_received += 1
		self.mesh.schedule_update()

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


#	async def console_pkt_log(self, room, key, count):
#		v = self.packet_log.get(key, count)
#		r = {
#		  'id': key,
#		  'type': 'pkt',
#		  'display_vals':  v
#		}
#		a_emit_to_room(r, room, self.steam)


#
# Packet
#
class Packet(Item):
	Number = 0
	def __init__(self, slnode = None, sl_op = None, rssi = 0, payload = None, pkt = None):
		Packet.Number += 1
#		self.steam = registry.find_by_id('Steam', 0)

		self.sl_op = None
		self.slid = None
		self.rssi = 0
		self.qos = 0
		self.pkt = None
		self.via = []
		self.payload = None

		super().__init__('Pkt', Packet.Number )
#X??		self.my_rooms = [str(Room("Pkt", self.key)), str(Room("Pkt", "*"))]
		self.name = "pktno-%s" % self.key

		logger.debug("Packet created: %s", self.name)

		if pkt is None:						# construct pkt
			self.slid = slnode.key
			self.sl_op = sl_op
			self.rssi = rssi + 256
			self.payload = payload
			logger.debug("SteamLinkPaktet payload = %s", payload);
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

			self.via = [] #N.B. node_routes[self.slid].via
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

