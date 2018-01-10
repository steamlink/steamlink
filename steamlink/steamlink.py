#!/usr/bin/env python3

# python library Stealink network

import struct
from asyncio import Queue
from queue import Empty, Full
import json
import time
import asyncio

from .timelog import TimeLog

import logging
logger = logging.getLogger(__name__)

from .util import phex

from .linkage import (
	registry,
	Room,
	Item,
)


_MQTT = None

def Attach(mqtt):
	global _MQTT
	_MQTT = mqtt
	logger.debug("steamlink: Attached mqtt client '%s'", _MQTT.name)


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

ritype = Steam, Mesh, Node, Pkt
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
			if struct.calcsize(SL_NodeCfgStruct.sfmt) != len(pkt):
				logger.error("NodeCfgStruct: packed messages length incorrect, wanted %s, got %s", struct.calcsize(SL_NodeCfgStruct.sfmt), len(pkt))
				raise SteamLinkError("packed messages length incorrect")
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
			'sleeps': self.sleeps,
			'pingable': self.pingable,
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
# Steam
#
class Steam(Item):
	console_fields = {
 	 "Name": "self.name",
 	 "Meshes": "' '.join(self.children)",
	 "Time": "time.asctime()",
	 "Load": '"%3.1f%%" % self.load',
	 }


	def __init__(self, conf):
		self.desc = conf['description']
		self.load = 0
		super().__init__('Steam', conf['id'])


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
			self.load = ((n_process_time - process_time) / delta ) * 100.0
			now = n_now
			process_time = n_process_time
			if logging.DBG == 0:	# N.B. reduce noise when debuging, i.e. no heartbeat
				self.schedule_update()


	def heartbeat(self):
		if not 'Node' in registry.get_itypes():
			return
		for node in registry.get_all('Node'):
			if node.is_overdue() and node.is_up():
				node.set_state("OVERDUE")
				node.schedule_update()
			if not node.is_up() and node.nodecfg.pingable:
				if node.last_packet_tx_ts + 60 < time.time():		# XXX var
					node.send_get_status()
		


	def save(self):
#		r = super().save()
		r = {}
		r['key'] = self.key
		r['name'] = self.name
		r['desc'] = self.desc
		return r


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
		self.packets_sent = 0
		self.packets_received = 0
		self.desc = "Description for mesh %s" % mesh_id

		super().__init__('Mesh', mesh_id)
		self.desc = "Description for %s" % self.name
		logger.info("Mesh created: %s", self)


	def mkname(self):
		return "Mesh%06x" % int(self.key)


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


	def save(self):
#		r = super().save()
		r = {}
		r['key'] = self.key
		r['name'] = self.name
		r['desc'] = self.desc
		return r

#
# Node
#
class Node(Item):
	console_fields = {
 	 "Name": "self.nodecfg.name",
	 "Description": "self.nodecfg.description",
	 "State": "self.state",
	 "Packets sent": "self.packets_sent",
	 "Packets received": "self.packets_received",
	 "SL ID": "self.slid",
	}
	UPSTATES = ["OK", "UP", "TRANSMITTING"]


	""" a node in a mesh set """
	def __init__(self, slid, nodecfg = None):
		logger.debug("Node createing : %s" % slid)
		if nodecfg is None:
			self.nodecfg = SL_NodeCfgStruct(slid, "Node%08x" % slid)
			logger.debug("Node config is %s", self.nodecfg)
		else:
			self.nodecfg = nodecfg
			self.name = nodecfg.name
		self.response_q = Queue(maxsize=1)

		self.slid = slid
		self.mesh_id = (slid >> 8)
		self.packets_sent = 0
		self.packets_received = 0
		self.pkt_numbers = {True: 0, False:  0}	# next pkt num for data, control pkts
		self.state = "UNKNOWN"
		self.last_packet_rx_ts = 0
		self.last_packet_tx_ts = 0
		self.last_packet_num = 0
		self.status = []
		self.via = []		# not initiatized
		self.tr = {}		# dict of sending nodes, each holds a list of (pktno, rssi)
		self.packet_log = TimeLog(MAX_NODE_LOG_LEN)

		self.mesh = registry.find_by_id('Mesh', self.mesh_id)
		if self.mesh is None:		# Auto-create Mesh
			logger.debug("Node %s: mesh %s autocreated", self.slid, self.mesh_id)
			self.mesh = Mesh(self.mesh_id)

		super().__init__('Node', slid, None, key_in_parent=self.mesh_id)

		logger.info("Node created: %s" % self)


	def set_pkt_number(self, pkt):
		dc =  pkt.is_data()
		self.pkt_numbers[dc] += 1
		if self.pkt_numbers[dc] == 0:
			self.pkt_numbers[dc] += 1	# skip 0
		return self.pkt_numbers[dc]


	def save(self):
#		r = super().save()
		r = {}
		r['key'] = self.key
		r['name'] = self.name
		r['slid'] = self.slid
		r['mesh_id'] = self.mesh_id
		r['via'] = self.via
		r['nodecfg'] = self.nodecfg.save()
		return r


	def mkname(self):
		if self.nodecfg is not None:
			return self.nodecfg.name
		return "Node%08x" % int(self.key)


	def get_firsthop(self):
		if len(self.via) == 0:
			firsthop = self.key
		else:
			firsthop = self.via[0]
		return firsthop


	def set_state(self, new_state):
		was_up = self.is_up()
		old_state = self.state
		self.state = new_state
		is_up = self.is_up()
	
		if not was_up and is_up:
			logger.info("node %s now online: %s -> %s", self, old_state, new_state)
		elif was_up and not is_up:
			logger.info("node %s now offline: %s -> %s", self, old_state, new_state)
		if was_up != is_up:
			# publish node state on some mqtt
			pass


	def is_up(self):
		return self.state in Node.UPSTATES


	def is_overdue(self):
		return (self.last_packet_rx_ts + self.nodecfg.max_silence) <= time.time()


	def publish_pkt(self, sl_pkt, sub="control"):
		self.log_pkt(sl_pkt)
		self.packets_sent += 1
		self.mesh.packets_sent += 1
		logger.debug("publish_pkt %s to node %s", sl_pkt, self.get_firsthop())
		_MQTT.publish(self.get_firsthop(), sl_pkt, sub=sub)
		self.last_packet_tx_ts = time.time()
		self.schedule_update()
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
		if not self.is_up(): return SL_OP.NC
		lorainit = struct.pack('<BLB', 0, 0, radio)
		logger.debug("send_set_radio_param: len %s, pkt %s", len(lorainit), lorainit)
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.SR, payload=lorainit)
		self.publish_pkt(sl_pkt)

		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
		return rc


	def send_testpacket(self, pkt):
		if not self.is_up(): return SL_OP.NC
		sl_pkt = Packet(slnode=self, sl_op=SL_OP.TD, payload=pkt)
		self.publish_pkt(sl_pkt)
		rc = self.get_response(timeout=SL_RESPONSE_WAIT_SEC)
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
		if pkt_num == last_packet_num + 1:	# proper squence
			return True
		if pkt_num == last_packet_num:		# duplicate
			logger.info("Node %s: received duplicate pkt %s", self, sl_pkt)
			return False
		if pkt_num == 1:					# remote restarted
			logger.error("Node %s: restarted with pkt 1", self)
			return True

		logger.error("%s: %s pkts missed before %s", self, pkt_num-(last_packet_num+1), sl_pkt)
		return True	#XXX


	def post_data(self, sl_pkt):
		""" handle incoming messages on the ../data topic """
		self.log_pkt(sl_pkt)
		if sl_pkt.is_data():
			if not self.check_pkt_num(sl_pkt):
				return	# duplicate
		else:
			logger.error("Node %s got control pkt %s", self, sl_pkt)

		self.packets_received += 1
		self.mesh.packets_received += 1
		self.last_packet_rx_ts = sl_pkt.ts

		logger.info("%s: received %s, op %s", self, sl_pkt, SL_OP.code(sl_pkt.sl_op))

		self.tr[sl_pkt.slid] = sl_pkt.rssi

		sl_op = sl_pkt.sl_op

#		if sl_op == SL_OP.ON:
#			logger.debug('post_data: slid 0x%0x ONLINE', int(self.key))
#			self.nodecfg = SL_NodeCfgStruct(pkt=sl_pkt.bpayload)
#			logger.debug("Node config is %s", self.nodecfg)
#
		if sl_op == SL_OP.ON:
			logger.debug('post_data: slid 0x%0x UP', int(self.key))
			self.nodecfg = SL_NodeCfgStruct(pkt=sl_pkt.bpayload)
			self.set_state("UP")
		elif sl_op == SL_OP.DS:
			logger.debug('post_data: slid 0x%0x status %s', int(self.key),sl_pkt.payload)
			self.status = sl_pkt.payload.split(',')

		elif sl_op == SL_OP.SS:
			logger.debug("post_data: slid 0x%0x status '%s'", int(self.key),sl_pkt.payload)
			self.set_state(sl_pkt.payload)

		elif sl_op in [SL_OP.AK, SL_OP.NK]:
			logger.debug('post_data: slid 0x%0x answer %s', int(self.key), SL_OP.code(sl_op))
			try:
				self.response_q.put(sl_op, block=False)
			except Full:
				logger.warning('post_data: node %s queue, dropping: %s', int(self.key), sl_pkt)
		elif sl_op == SL_OP.TR:
			logger.debug('post_data: node %s test msg', sl_pkt.payload)

			try:
				test_pkt = TestPkt(pkt=sl_pkt.payload)
			except ValueError as e:
				logger.warning("post_incoming: cannot identify test data in %s", sl_pkt.payload)
				return

			test_pkt.set_receiver_slid(sl_pkt.via)
			test_pkt.set_rssi(sl_pkt.rssi)
			if not test_pkt.pkt['slid'] in self.tr:
				self.tr[test_pkt.pkt['slid']] = []
			self.tr[test_pkt.pkt['slid']].append((test_pkt.pkt['pktno'], test_pkt.pkt['rssi']))
#			sl_log.post_incoming(test_pkt)

		# any pkt from node indicates it's up
		if not self.is_up():
			self.set_state('TRANSMITTING')

		self.schedule_update()
		self.mesh.schedule_update()


	def get_response(self, timeout):
		try:
			data = self.response_q.get(block=True, timeout=timeout)
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
#		  'id': key,
#		  'type': 'pkt',
#		  'display_vals': { 'data': v }
#		}
#		emit_to_room(r, room)


#	async def console_pkt_log(self, room, key, count):
#		v = self.packet_log.get(key, count)
#		r = {
#		  'id': key,
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
	data_header_fmt = '<BLHBB%is'		# op, slid, pkt_num, rssi, qos, payload"
	control_header_fmt = '<BLHB%is'		# op, slid, pkt_num, qos, payload"

	def __init__(self, slnode = None, sl_op = None, rssi = 0, payload = None, pkt = None):
		self.rssi = 0
		self.qos = 0
		self.via = []
		self.payload = None
		self.itype = "Pkt"
		self.ts = time.time()
		self.nodecfg = None

		if pkt is not None:					# deconstruct pkt
			if not self.deconstruct(pkt):
				logger.error("deconstruct pkt to short: %s", len(pkt))
				raise SteamLinkError("deconstruct pkt to short");
		else:								# construct pkt
			self.construct(slnode, sl_op, rssi, payload)

		self.node = registry.find_by_id('Node', self.slid)
		if self.node is None:		# Auto-create node
			self.node = Node(self.slid, self.nodecfg)
		Packet.PacketID += 1
		super().__init__('Pkt', Packet.PacketID, key_in_parent=self.slid )
		self.name = "N%s_P%s" % (self.slid, self.pkt_num)
		logger.debug("pkt %s: node %s: %s", self,  self.node, SL_OP.code(self.sl_op))

		if pkt is not None:					# deconstruct pkt
			if self.node.via == []:
				self.node.via = self.via
			elif self.node.via != self.via:
				logger.warning("node %s routing changed, was %s is now %s", \
						self.node, self.node.via, self.via)
				self.node.via = self.via




	def is_data(self, sl_op = None):
		if sl_op is None:
			sl_op = self.sl_op
		if sl_op is None:
			logger.error("packet op not yet known")
			raise SteamLinkError("packet op not yet known");
		return (sl_op & 0x1) == 1


	def construct(self, slnode, sl_op, rssi, payload):
		self.slid = int(slnode.key)
		self.sl_op = sl_op
		self.rssi = rssi + 256
		self.payload = payload
		logger.debug("SteamLinkPaktet payload = %s", payload);
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
		if self.is_data():
			sfmt = Packet.data_header_fmt % len(self.bpayload)
			logger.debug("pack: %s %s %s %s %s %s", self.sl_op, self.slid, self.pkt_num, self.rssi, self.qos, self.bpayload)
			self.pkt = struct.pack(sfmt,
					self.sl_op, self.slid, self.pkt_num, 256 - self.rssi, self.qos, self.bpayload)
		else:
			logger.debug("pkt %s for %s", SL_OP.code(self.sl_op), slnode)
			sfmt = Packet.control_header_fmt % len(self.bpayload)
			self.pkt = struct.pack(sfmt,
					self.sl_op, self.slid, self.pkt_num, self.qos, self.bpayload)
			if len(slnode.via) > 0:
				for via in slnode.via:
					if via == self.slid:
						break
					self.bpayload = self.pkt
					sfmt = Packet.control_header_fmt % len(self.bpayload)
					self.pkt = struct.pack(sfmt, SL_OP.BN, via, 0, self.qos, self.bpayload)
			for l in phex(self.pkt, 4):
				logger.debug("pkt c:  %s", l)




	def deconstruct(self, pkt):
		self.pkt = pkt
		for l in phex(pkt, 4):
			logger.debug("pkt:  %s", l)

		if pkt[0] == SL_OP.BS:		# un-ecap all
			while pkt[0] == SL_OP.BS:
				payload_len = len(pkt) - struct.calcsize(Packet.data_header_fmt % 0)
				sfmt = Packet.data_header_fmt % payload_len
				self.sl_op, slid, self.pkt_num, self.rssi, self.qos, self.bpayload \
						= struct.unpack(sfmt, pkt)

				self.via.append(slid)
				pkt = self.bpayload
				logger.debug("pkt encap BS from %s, len %s rssi %s", slid, len(pkt), self.rssi)
			self.rssi = self.rssi - 256

		if len(pkt) < struct.calcsize(Packet.data_header_fmt % 0):
			logger.error("deconstruct pkt to short: %s", len(pkt))
			return False;
		if self.is_data(pkt[0]):
			payload_len = len(pkt) - struct.calcsize(Packet.data_header_fmt % 0)
			sfmt = Packet.data_header_fmt % payload_len
			self.sl_op, self.slid, self.pkt_num, rssi, self.qos, self.bpayload \
						= struct.unpack(sfmt, pkt)
		else:
			payload_len = len(pkt) - struct.calcsize(Packet.control_header_fmt % 0)
			sfmt = Packet.control_header_fmt % payload_len
			self.sl_op, self.slid, self.pkt_num, self.qos, self.bpayload \
						= struct.unpack(sfmt, pkt)
		self.payload = None

		if self.sl_op == SL_OP.ON:
			self.nodecfg = SL_NodeCfgStruct(pkt=self.bpayload)
			logger.debug("Node config is %s", self.nodecfg)

		if len(self.bpayload) > 0:
			try:
				self.payload = self.bpayload.decode('utf8').strip('\0')
			except Exception as e:
				pass

		if self.is_data():
			self.via.append(self.slid)
		return True

	def save(self):
#		r = super().save()
		r = {}
		r['sl_op'] = self.sl_op
		r['slid'] = self.slid
		r['ts'] = self.ts
		r['rssi'] = self.rssi
		r['qos'] = self.qos
		r['payload'] = self.payload
		r['bpayload'] = repr(self.bpayload)
		return r


	def post_data(self):
		self.node.post_data(self)

	def o__str__(self):
		if self.slid is None:
			via = "-%s-" % self.key
		else:
			via = "0x%0x" % self.slid
		if len(self.via) > 0:
			for v in self.via[::-1]: via += "->0x%0x" % v
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
		return data


	def get_room_list(self):
		rooms = []
		rooms.append( "%s_*" % (self.itype))
		# Packets don't have a 'header' room
#		rooms.append( "%s_%s" % (self.itype, self.key))
		if self.parent is not None:
			rooms.append( "%s_%s_*" % (self.parent.itype, self.parent.key))
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
		self.pkt_inq = Queue()
		self.nodes_online = 0


	def log_state(self, slid, new_state):
		logger.debug("logdata node 0x%0x %s", slid, new_state)
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


