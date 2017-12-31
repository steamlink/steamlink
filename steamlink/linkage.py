
import asyncio

import logging
logger = logging.getLogger()



# Globals, initialized by attach
_WEBAPP = None

def Attach(app):
	global _WEBAPP
	if _WEBAPP is not None:
		logger.error("Linkage: Attach already done")
		return
	_WEBAPP = app
	logger.debug("linkage: Attached webapp '%s'", _WEBAPP.name)

registry = None

#
# Registry
#
class Registry:
	def __init__(self, ItemTypes):
		self.ItemTypes = ItemTypes
		self.name_idx = {}
		for lvl in self.ItemTypes:
			self.name_idx[lvl] = {}
		self.id_idx = {}
		for lvl in self.ItemTypes:
			self.id_idx[lvl] = {}

	def register(self, item):
		if logging.DBG > 2: logger.debug("Registry: register %s", item)
		if logging.DBG > 2: logger.debug("Registry: 0 registered %s", item)
		self.name_idx[item.itype][item.name] = item
		self.id_idx[item.itype][item.key] = item
		if logging.DBG > 2: logger.debug("Registry: registered %s", item)

	def unregister(self, item):
		if logging.DBG > 2: logger.debug("Registry: unregister %s", item)
		del self.name_idx[item.itype][item.name]
		del self.id_idx[item.itype][item.key]


	def get_parent_type(self, itype):
		try:
			i = self.ItemTypes.index(itype)
		except:
			return None
		if i == 0:
			return None
		return self.ItemTypes[i-1]


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
		t = self.id_idx[itype].get(str(Id), None)
		if logging.DBG > 2: logger.debug("find_by_id %s %s = %s", itype, Id, str(t))
		return t


#
def SetRegistry(ItemTypes):
	global registry
	registry = Registry(ItemTypes)



#
# BaseItem
#
class BaseItem:
	def __init__(self, itype, key, name = None, key_in_parent = 0):
		self.itype = itype
		self.key = str(key)		# assure type identity
		if name is None:
			self.name = self.mkname()
		else:
			self.name = name
		self.parent = None
		self.children = {}
		logger.debug("BaseItem: created %s", self)
		registry.register(self)


	def __del__(self):
		logger.debug("BaseItem: __del__ %s", self)


	def delete(self):
		logger.debug("BaseItem %s: delete", self)
		if registry.find_by_id(self.itype, self.key) is not None:
			registry.unregister(self)
			self.parent = None
			logger.debug("BaseItem: deleted %s", self)


	def mkname(self):
		return "%s:%s" % (self.itype, self.key)


	def __str__(self):
		return "%s %s(%s)" % (self.itype, self.name, self.key)


#
# Item
#
class Item(BaseItem):
	def __init__(self, itype, key, name = None, key_in_parent = 0):
		super().__init__(itype, key, name)

		self.parent = self.get_parent(key_in_parent)
		if self.parent is not None:
			self.parent.add_child(self)

		self.my_room_list = []
		for r in self.get_room_list():
			logger.debug("Item %s: add room %s", self, r)
			room = registry.find_by_id('Room', r)
			if room is None:
				room = Room(sroom = r)
			self.my_room_list.append(room)
			room.add_item(self)

		self.schedule_update()


	def delete(self):
		logger.debug("Item %s: delete", self)
		if registry.find_by_id(self.itype, self.key) is not None: # recursive delete
			for room in self.my_room_list:
				logger.debug("Item %s: del room %s", self, room)
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


	def get_parent(self, key_in_parent = 0):
		ptype = self.get_parent_type()
		if ptype is None:
			p = None
		else:
			p = registry.find_by_id(ptype, key_in_parent)
		if logging.DBG > 1: logger.debug("Item: get_parent %s): %s", self, str(p))
		return p


	def add_child(self, item):
		logger.debug("Item: child %s added to %s", item.key, self)
		self.children[item.key] = item
		self.schedule_update()


	def del_child(self, item):
		logger.debug("Item: child %s delete from %s", item.key, self)
		del self.children[item.key]
		self.schedule_update()


	def schedule_update(self):
		logger.debug("Item %s: schedule_update", self.name)
		for room in self.my_room_list:
			logger.debug("Item: schedule_update for item %s in room %s", self, room.name)
			room.roomitems[self.key].schedule_update(False)


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
		self.pack = {
			'id': self.item.name,
			'type': self.item.itype,
			'header': self.room.is_header(),
		 	'display_vals': {},
		}
		self.cache = {}
		self.upd_in_progress = False


	def console_update(self, force):
		if self.deleted:
			data_to_emit = {}
		elif not force and self.cache != {}:
			data_to_emit = self.cache
		else:
			data_to_emit = self.item.gen_console_data()
			self.cache = data_to_emit
		self.pack['display_vals'] = data_to_emit
		logger.debug("console_update ROOM %s ITEM %s DATA %s", self.room, self.item, self.pack)
		return self.pack


	async def schedule_future_update(self, wait):
		await asyncio.sleep(wait)
		self.future_update = False
		self.schedule_update(False)


	def schedule_update(self, force):
		if self.upd_in_progress:
			return
#		if not _WEBAPP:
#			logger.warning("RoomItem: update before _WEBAPP")
#			return
		next_update = (self.last_update + _WEBAPP.minupdinterval) - _WEBAPP.loop.time()
		if not force and next_update > 0:
			if not self.future_update:
				asyncio.ensure_future(self.schedule_future_update(next_update))
				self.future_update = True
			return

		self.upd_in_progress = True
		_WEBAPP.schedule_update(self, True)


	def update_sent(self):
		self.upd_in_progress = False
		self.last_update = _WEBAPP.loop.time()


#
# Room
#
class Room(BaseItem):
	def __init__(self, lvl = None, rkey = None, detail = None, sroom = None):

		if sroom is not None:
			l = sroom.split('_')
			if len(l) < 2 or len(l) > 3:
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
			self.sroom = self.mksroom()
		super().__init__('Room', sroom)

		self.last_update = 0		# room's last update time stamp
		self.future_update = 0		# room's next update time stamp
		self.members = {}			# fake dict: web session in room
		self.roomitems = {}				# item keys in room


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


	def schedule_update(self):
		for roomitem in self.roomitems:
			self.roomitems[roomitem].schedule_update(True)

			
	def mkdsroom(self):
		if not self.detail is None:
			return "%s_%s_%s" % (self.lvl, self.rkey, self.detail)
		return "%s_%s" % (self.lvl, self.rkey)


