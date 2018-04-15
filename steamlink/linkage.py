
import asyncio
import shelve
import os
import socket

import logging
logger = logging.getLogger()

# Globals, initialized by attach
_WEBAPP = None
_DB = None

def Attach(app, db):
	global _WEBAPP, _DB
	if _WEBAPP is not None:
		logger.error("Linkage: Attach already done")
		return
	_WEBAPP = app
	_DB = db

	logger.debug("linkage: Attached apps '%s, %s'", _WEBAPP.name, _DB.name)


import yaml
from yaml import Loader, Dumper

#
# Registry
#
class Registry:
	def __init__(self):
		logger.debug("Registry: instance")


	def open(self, fname):
		# Note: load from file not implememted, only save
		logger.debug("Registry: open")
		self.fname = fname
		if fname is None:
			self.reg = {'name_idx': {}, 'id_idx': {}, 'ItemTypes': []}
			for itype in ['Steam', 'Mesh', 'Node', 'Pkt', 'RoomItem', 'Room']:
				self.reg['ItemTypes'].append(itype)
				self.reg['name_idx'][itype] = {}
				self.reg['id_idx'][itype] = {}
		else:
			self.reg = self.load()


	def close(self):
		if self.fname is None:
			return
		logger.info("saving registry")
		data = self.save()
		if logging.DBG > 2:
			import pprint
			pp = pprint.PrettyPrinter(indent=4)
			pp.pprint(data)
		logger.info("writing registry to %s", self.fname)
		with open(self.fname, 'w') as outfile:
			yaml.dump(data, outfile, default_flow_style=False)


	def load(self):
		r =  {'name_idx': {}, 'id_idx': {}, 'ItemTypes': []}
		return r


	def register(self, item):
		if not item.itype in self.reg['ItemTypes']:
			self.reg['ItemTypes'].append(item.itype)
			self.reg['name_idx'][item.itype] = {}
			self.reg['id_idx'][item.itype] = {}
		if logging.DBG > 2: logger.debug("Registry: register %s", item)
		self.reg['name_idx'][item.itype][item.name] = item
		self.reg['id_idx'][item.itype][item.key] = item


	def unregister(self, item):
		if logging.DBG > 2: logger.debug("Registry: unregister %s", item)
		del self.reg['name_idx'][item.itype][item.name]
		del self.reg['id_idx'][item.itype][item.key]


	def get_parent_type(self, itype):
		try:
			i = self.reg['ItemTypes'].index(itype)
		except:
			return None
		if i == 0:
			return None
		return self.reg['ItemTypes'][i-1]


	def get_itypes(self):
		return self.reg['ItemTypes']


	def get_all(self, itype):
		r = []
		for key in self.reg['id_idx'][itype]:
			r.append(self.reg['id_idx'][itype][key])
		return r


	def find_by_name(self, itype, name):
		t = self.reg['name_idx'][itype].get(name, None)
		return t


	def find_by_id(self, itype, Id):
		try:
			t = self.reg['id_idx'][itype].get(str(Id), None)
		except KeyError as e:
			logger.warning("find_by_id: %s not in id_idx", e)
			t = None
			
		if logging.DBG > 2: logger.debug("find_by_id %s %s = %s", itype, Id, str(t))
		return t


	def save(self):
		r = {}
		for itype in self.reg['ItemTypes']:
			kd = {}
			for k in self.reg['id_idx'][itype]:
				kd[k] = self.reg['id_idx'][itype][k].save()
			r[itype] = kd		
		
		return r


#
# Registry linkage
#
registry = Registry()

def OpenRegistry(fname):
	registry.open(fname)

def CloseRegistry():
	logger.info("closing registry")
	registry.close()
	logger.info("registry closed")


#
# BaseItem
#
class BaseItem:
	def __init__(self, itype, key, name = None):
		self.itype = itype
		self.key = str(key)		# assure type identity
		if name is None:
			self.name = self.mkname()
		else:
			self.name = name
		if logging.DBG >= 1: logger.debug("BaseItem: created %s", self)
		registry.register(self)


	def __del__(self):
		if logging.DBG > 2: logger.debug("BaseItem: __del__ %s", self)
		logger.info("BaseItem: __del__ %s", self)


	def delete(self):
		if logging.DBG > 2: logger.debug("BaseItem %s: delete", self)
		if registry.find_by_id(self.itype, self.key) is not None:
			registry.unregister(self)
			self.parent = None
			if logging.DBG > 2: logger.debug("BaseItem: deleted %s", self)


	def mkname(self):
		return "%s:%s" % (self.itype, self.key)


	def __str__(self):
		try:
			return "%s %s(%s)" % (self.itype, self.name, self.key)
		except:
			return "SomeBaseItem"


	def save(self):
		r = self.__dict__.copy()
		return r


	def getkeyfield(self):
		return "key"	#CU


#
# Item
#
class Item(BaseItem):
	def __init__(self, itype, key, name = None, key_in_parent = None):
		self.parent = None
		self.children = {}
		self.my_room_list = []
		super().__init__(itype, key, name)
		if not key_in_parent is None:
			self.set_parent(key_in_parent)
		self.set_rooms()


	def set_parent(self, key_in_parent):
		self.parent = self.get_parent(key_in_parent)
		if self.parent is not None:
			self.parent.add_child(self)


	def set_rooms(self):
		for r in self.get_room_list():
			if logging.DBG >= 1: logger.debug("Item %s: add room %s", self, r)
			room = registry.find_by_id('Room', r)
			if room is None:
				room = Room(sroom = r)
			self.my_room_list.append(room)
			room.add_item(self)

		self.schedule_update()


	def delete(self):
		if logging.DBG > 2: logger.debug("Item %s: delete", self)
		if registry.find_by_id(self.itype, self.key) is not None: # recursive delete
			for room in self.my_room_list:
				if logging.DBG > 2: logger.debug("Item %s: del room %s", self, room)
				room.del_item(self)
			if self.parent is not None:
				self.parent.del_child(self)
			children = list(self.children.keys())	# NB. dict shrinks!
			for child in children:
				self.children[child].delete()
			self.children = {}
			self.parent = None
			self.schedule_update()	#XXX update type: del?
			super().delete()


	def get_parent_type(self):
		return registry.get_parent_type(self.itype)


	def get_parent(self, key_in_parent):
		ptype = self.get_parent_type()
		if ptype is None:
			p = None
		else:
			p = registry.find_by_id(ptype, key_in_parent)
		if logging.DBG > 1: logger.debug("Item: get_parent (%s) %s: %s", self, ptype, str(p))
		return p


	def add_child(self, item):
		if logging.DBG > 2: logger.debug("Item: child %s added to %s", item.key, self)
		self.children[item.key] = item
		self.schedule_update()


	def del_child(self, item):
		if logging.DBG > 2: logger.debug("Item: child %s delete from %s", item.key, self)
		del self.children[item.key]
		self.schedule_update()


	def schedule_update(self):
		if logging.DBG > 2: logger.debug("Item %s: schedule_update", self.name)
		for room in self.my_room_list:
			if logging.DBG > 2: logger.debug("Item: schedule_update for item %s in room %s", self, room.name)
			room.roomitems[self.key].push_update(False)


	def gen_console_data(self):
		cs = []
		for c in self.children:
			cs.append(str(self.children[c]))
		data = {
			'name': self.name,
			'type': self.itype,
			'id': self.key,
			'children': str(cs),
		}
		return data


	def get_room_list(self):
		rooms = []
		rooms.append( "%s_*" % (self.itype))
		rooms.append( "%s_%s" % (self.itype, self.key))
		if self.parent is not None:
			rooms.append( "%s_%s_*" % (self.parent.itype, self.parent.key))
		return rooms


	def save(self):
		r = super().save()
		if self.parent:
			r['parent'] = self.parent.key
		if self.children:
			r['children'] = list(self.children.keys())
		return r

#
# RoomItem
#
class RoomItem:
	def __init__(self, room, item):
		self.room = room
		self.item = item
		# stash for when item is deleted
		self.itemname = "%s" % item.name
		self.deleted = False

		self.last_update = 0		# roomitem's last update time stamp
		self.future_update = False	
#		self.pack = {
#			'name': self.item.name,		#XXX? extra fields
#			'type': self.item.itype,
#			'id': self.item.key,
#			'header': self.room.is_header(),
#		 	'display_vals': {},
#		}
		self.cache = {}
		self.upd_in_progress = False


	def __getstate__(self):
		r = self.__dict__.copy()
#		r = {'room': self.room.key, 'item': self.item.key}
		r['room'] = self.room.key 
		r['item'] = self.item.key
		return r

	def __repr__(self):
		return str(self.__getstate__())

	def console_update(self, force):
		if self.deleted:
			data_to_emit = {}
		elif not force and self.cache != {}:
			data_to_emit = self.cache
		else:
			data_to_emit = self.item.gen_console_data()
			self.cache = data_to_emit
#		self.pack['display_vals'] = data_to_emit
		self.pack = data_to_emit
		if logging.DBG >= 1: logger.debug("console_update ROOM %s ITEM %s DATA %s", self.room, self.item, self.pack)
		return self.pack


	async def schedule_future_update(self, wait):
		await asyncio.sleep(wait)
		self.future_update = False
		self.push_update(False)


	def push_update(self, force, sroom = None):
		if self.upd_in_progress or _WEBAPP is None:
			return
		next_update = (self.last_update + _WEBAPP.minupdinterval) - _WEBAPP.loop.time()
		if not force and next_update > 0:
			if not self.future_update:
				asyncio.ensure_future(self.schedule_future_update(next_update))
				self.future_update = True
			return

		if sroom is None:
			sroom = self.room.sroom
		self.upd_in_progress = True
		_WEBAPP.queue_item_update(self, sroom, True)


	def update_sent(self):
		self.upd_in_progress = False
		self.last_update = _WEBAPP.loop.time()

#
# MemberRoom
#
class MemberRoom:
	def __init__(self, room, sid):
		self.m_room = room
		self.m_sid = sid
		self.m_roomitem_keys = []
		self.cur_key = None
		self.count = 1


	def set_position(self, key, count = 20):
		self.cur_key = key
		self.count = count
		keys = self.m_room.get_roomitem_keys()
		if key == "FIRST":
			key = keys[0]
		elif key == "LAST":
			key = keys[-1]
		if not key in keys:
			self.m_roomitem_keys = []
		else:
			k_idx = keys.index(key)
			if count >= 0:
				self.m_roomitem_keys = keys[k_idx:k_idx+count]
			else:
				end = k_idx + 1
				start = k_idx + count + 1
				if start < 0:
					end = end - start + 1
					start = 0
				self.m_roomitem_keys = keys[start:end]
				logger.debug("set_position: key %s start %s end %s", key, start, end)
			# send out the item
			for k in self.m_roomitem_keys:
				self.m_room.roomitems[k].push_update(False, self.m_sid)

	def has_roomitem(self, key):
		return key in self.m_roomitem_keys

#
# Room
#
class Room(BaseItem):
	def __init__(self, ritype = None, rkey = None, detail = None, sroom = None):

		self.stream_tag = None
		if sroom is not None:
			l = sroom.split('_')
			if len(l) < 2 or len(l) > 3:
				logger.error("Room: sroom string invalid: %s" % sroom)
				l.append("*")	# XXX
			self.ritype = l[0]
			self.rkey = l[1]
			self.detail = None if len(l) < 3 else l[2]
			self.sroom = sroom
		else:
			self.ritype = ritype
			self.rkey = rkey
			self.detail = detail
			self.sroom = self.mksroom()
		super().__init__('Room', sroom)

		self.members = {}			# fake dict: web session in room
		self.roomitems = {}			# RoomItem keys in room


	def get_roomitem_keys(self):
		return list(self.roomitems.keys())


	def is_item_room(self):
		return self.detail != None


	def is_header(self):
		if self.detail == '*' or self.rkey == '*':
			return False
		return True


	def no_key(self):
		return "%s_*" % (self.ritype)


	def full_key(self):
		return self.sroom


	def add_member(self, sid, key = None, count = 0 ):
		if sid in self.members:
			if self.members[sid] == sid and key is not None:
				logger.info("room add_member: id %s making room %s private", sid, self)
			else:
				logger.error("room add_member: id %s already a member in room %s", sid, self)
				return "NAK"
		if key is None:
			self.members[sid] = sid
		else:
			self.members[sid] = MemberRoom(self, sid)


	def del_member(self, sid):
		if not sid in self.members:
			logger.error("room del_member: id %s not a member in room %s", sid, self)
		else:
			del self.members[sid] 


	def is_private(self, sid):
		if not sid in self.members or type(self.members[sid]) == type(""):
			return False
		return isinstance(self.members[sid], MemberRoom)


	def add_item(self, item):
		if item.key in self.roomitems:
			logger.error("room %s add_item: id %s already an item in room", self, item)
			return
		self.roomitems[item.key] = RoomItem(self, item)


	def del_item(self, item):
		if not item.key in self.roomitems:
			logger.error("room %s del_member: item %s not an item in room", self, item)
		else:
			self.roomitems[item.key].deleted = True
			self.roomitems[item.key].item = None


	def schedule_update(self, rsid = None):
		limit = 50		# XXX todo: make variable
		for roomitem in self.get_roomitem_keys():
			self.roomitems[roomitem].push_update(True, rsid)
			for sid in self.members:
				if self.is_private(sid) and self.members[sid].has_roomitem(roomitem):
					self.roomitems[roomitem].push_update(True, sid)
			limit -= 1
			if limit == 0:
				break
					

			
	def mksroom(self):
		if not self.detail is None:
			return "%s_%s_%s" % (self.ritype, self.rkey, self.detail)
		return "%s_%s" % (self.ritype, self.rkey)

