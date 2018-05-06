
import asyncio
from asyncio import Queue
import shelve
import os
import sys
import socket
import time

import logging
logger = logging.getLogger()

# Globals, initialized by attach
_WEBAPP = None
_DB = None

def Attach(webapp, db):
	global _WEBAPP, _DB
	if _WEBAPP is not None:
		logger.error("Linkage: Attach already done")
		return
	_WEBAPP = webapp
	_DB = db

	if _WEBAPP is None:
		webapp_name = "-"
	else:
		webapp_name = _WEBAPP.name
	if _DB is None:
		db_name = "-"
	else:
		db_name = _DB.name

	logger.debug("linkage: Attached apps '%s, %s'", webapp_name, db_name)


import yaml
from yaml import Loader, Dumper

class CSearchKey:
	def __init__(self, table_name, key_field, restrict_by, start_key, start_item_number, end_key, count, stream_tag):

		self.table_name = table_name
		self.key_field = key_field
		self.start_key = start_key
		self.start_item_number = start_item_number
		self.end_key = end_key
		self.count = count
		self.stream_tag = stream_tag

		self.at_start = False
		self.at_end = False
		self.search_id =  self.__repr__()	#used to index CSearches 

		self.total_item_count = None

		self.restrict_by = restrict_by

		self.restrict_field = None
		self.restrict_value = None

	def __repr__(self):
		return  "%s(%s:%s:%s)_%s" %\
		 (self.table_name, self.key_field, self.start_key, self.end_key, self.stream_tag)

	def __str__(self):
		at_start = "S" if self.at_start else "-"
		at_end = "E" if self.at_end else "-"
		return "CS: %s %s(%s->%s) -%s- tot %s  cnt %s %s%s" % \
		 (self.table_name, self.key_field, self.start_key, self.end_key, 
				self.restrict_by, self.total_item_count, self.count, at_start, at_end)
#
# CSearch 
#
class CSearch:
	def __init__(self,  webnamespace, table, csearchkey):
		
		self.webnamespace = webnamespace
		self.csearchkey = csearchkey
		self.search_id = csearchkey.search_id
		self.clients = {}		# users who registed, sid, stream_tag
		self.cs_items = {}		# current list if items in sarch, key if key_field

		self.table = table
		if csearchkey.key_field is None:
			csearchkey.key_field = self.table.keyfield
		for item in self.table.get_range(self.csearchkey):
			self.add_item(item)
		if logging.DBG > 2: logger.debug("CSearch csearch key: %s", str(csearchkey))


	def __str__(self):
		return "CSearch[%s]" % self.search_id


	def add_item(self, item):
		if logging.DBG > 2: logging.debug("CSearch '%s' add_item %s in key %s", self.search_id, item, self.csearchkey.key_field)
		self.cs_items[item.__dict__[self.csearchkey.key_field]] = CSearchItem(self, item)


	def drop_item(self, item):
		if logging.DBG > 2: logger.debug("CSearch '%s' drop_item %s", self.search_id, item)
		self.cs_items[item.__dict__[self.csearchkey.key_field]].deleted = True
#?		del self.cs_items[item.__dict__[self.csearchkey.key_field]]


	def add_sid(self, sid):
		if logging.DBG > 2: logger.debug("CSearch '%s' add_sid %s csearchkey %s", self.search_id, sid, self.csearchkey)
		self.clients[sid] = self.csearchkey.stream_tag
		self.webnamespace.enter_room(sid, self.search_id, \
			 self.webnamespace.namespace)
		return self.csearchkey


	def drop_sid(self, sid):
		if sid in self.clients:
			if logging.DBG > 2: logger.debug("CSearch '%s' drop_sid %s", self.search_id, sid)
			del self.clients[sid]
			self.webnamespace.leave_room(sid, self.search_id, \
				 self.webnamespace.namespace)


	def num_clients(self):
		return len(self.clients)


	def check_restrictions(self, restrict_by, item):
		for restrict in restrict_by:
			field =  restrict['field_name'] 
			op =  restrict['op'] 
			value =  restrict['value'] 
			ex = "item['%s'] %s %s" % (field, op, repr(value))
			return eval(ex)


	def check_csearch(self, op, item):
		""" check if item matches any registered searches, 
			schedule update for all search clients it matched 
			op is 'ins', 'upd' or 'del'
			note that item is updated or inserted in db before check_csearch
			but the delete will happen after
		"""
# find csitem for item
		if logging.DBG >= 2: logger.debug("CSearch %s check_csearch op %s for %s", self.search_id, op, item)
		item_search_key = item.__dict__[self.csearchkey.key_field]
		push = False
		go = self.check_restrictions(self.csearchkey.restrict_by, item.__dict__)
		if not go:
			return

		if not item_search_key in self.cs_items:
			if op in ['ins']:
				if  self.csearchkey.at_end \
					and  item_search_key > self.csearchkey.end_key:
					push = True
					self.csearchkey.end_key = item_search_key
					self.add_item(item)
				elif not push and op in ['ins' ,'upd'] \
					and self.csearchkey.at_end \
					and  item_search_key < self.csearchkey.start_key:
					push = True
					self.csearchkey.start_key = item_search_key
					self.add_item(item)
		elif item_search_key >= self.csearchkey.start_key and \
				item_search_key <= self.csearchkey.end_key:
			if op == 'upd':
				pass
			elif op == 'ins':
				self.add_item(item)
			elif op == 'del':
				self.drop_item(item)

			push = True

		if push:
			self.cs_items[item_search_key].push_update(False)
		return 


	def force_update(self, sid):
		if not sid in self.clients:
			return
		if logging.DBG > 1: logger.debug("force_update ")
		for cs in self.cs_items:
			self.cs_items[cs].push_update(True)

#
# CSearchItem
#
class CSearchItem:
	def __init__(self, csearch, item):
		self.csearch = csearch
		self.item = item
		# stash for when item is deleted
#		self.itemname = "%s" % item.name
		self.deleted = False

		self.last_update = 0		# csearchitem's last update time stamp
		self.future_update = False	
		self.cache = {}
		self.upd_in_progress = False


	def __getstate__(self):
		r = self.__dict__.copy()
		r['csearch'] = self.csearch.key 
		r['item'] = self.item.key
		return r

	def __repr__(self):
		return str(self.__getstate__())

	def console_update(self, force):
		if not force and self.cache != {}:
			data_to_emit = self.cache
		else:
			data_to_emit = self.item.gen_console_data()
			self.cache = data_to_emit
		if self.deleted:
			data_to_emit['_del_key'] = 1

		if logging.DBG >  1: logger.debug("console_update CSearch %s Item %s Data %s", self.csearch, self.item, str(data_to_emit)[:32]+"...")
		return data_to_emit


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

		self.upd_in_progress = True
		_WEBAPP.queue_item_update(self, True)


	def update_sent(self):
		self.upd_in_progress = False
		self.last_update = _WEBAPP.loop.time()



#
# OCache
#
class OCache(dict):
	def __init__(self, tablename, max_entries = 1000):
		self.tablename = tablename
		self.max_entries = max_entries
		self.ts = {}
		super().__init__()

	def __getitem__(self, key):
		self.ts[key] = time.time()
		return super().__getitem__(key)

	def __setitem__(self, key, value):
		super().__setitem__(key, value)
		self.ts[key] = time.time()
		if logging.DBG > 2: logger.debug("cache added %s %s at %s", key, value, self.ts[key])
		if len(self) > self.max_entries:
			if logging.DBG > 2: logger.debug("Cleaning!")
			self.clean()

	def clean(self):
		to_prune =  len(self) - self.max_entries
		for p in range(to_prune):
			ts = time.time()
			d = None
			for i in self.keys():
				if sys.getrefcount(super().__getitem__(i)) == 2:	# N.B sys.getrefcount and  self[i]
					if ts > self.ts[i]:
						d = i
						ts = self.ts[i]
			if d is None:
				logger.warning("cache for index '%s' overcommited", self.tablename)
				break
			del self[d]
			del self.ts[d]
		logger.debug("%s", self.status())
		return 

	def status(self):
		in_use = 0
		for i in self.keys():
			if sys.getrefcount(super().__getitem__(i)) != 2:
				in_use += 1
		return "cache '%s' has %s entries of %s max (%s%%)" % \
			(self.tablename, in_use, self.max_entries, (100.0 * in_use / self.max_entries))


#
# Table
#
class Table:
	""" provide an index over items """
	index = None
	def __init__(self, itemclass, keyfield):
		self.itemclass = itemclass
		self.keyfield = keyfield
		self.csearches = {}


	def add_csearch(self, webnamespace, csearchkey, sid):

		srch_id = csearchkey.search_id
		if logging.DBG > 2: logger.debug("table add_csearch search_id '%s': %s", srch_id, str(csearchkey))
		if not srch_id in self.csearches:
			self.csearches[srch_id] = CSearch(webnamespace, self, csearchkey)
		csearchkey = self.csearches[srch_id].add_sid(sid)
		return csearchkey


	def drop_sid_from_csearch(self, sid):
		del_list = []
		for cs in self.csearches:
			self.csearches[cs].drop_sid(sid)
			if self.csearches[cs].num_clients() == 0:
				del_list.append(cs)
		for cs in del_list:
			del self.csearches[cs]


	def find(self, key, keyfield = None):
		pass

	def find_one(self, key, keyfield = None):
		pass

	def insert(self, item):
		self.check_csearch('ins', item)

	def update(self, item):
		self.check_csearch('upd', item)

	def delete(self, item):
		self.check_csearch('del', item)

	def check_csearch(self, op, item):
		for cs in self.csearches:
			self.csearches[cs].check_csearch(op, item)
		 
	def register(self, item):
		pass


	
class DbTable(Table):
	""" database based Table """

	def __init__(self, itemclass, keyfield, tablename):
		self.tablename = tablename
		self.cache = OCache(tablename, 1000)
		self.dbtable = None
		self.dbtable = _DB.table(self.tablename)
		super().__init__(itemclass, keyfield)
	

	def register(self, item):
		""" backload item from db if it exists, otherwise insert in db """
		if item._key is None:		#  created by find
			logger.debug("register %s not registred", self.tablename, item)
			return False
		if logging.DBG > 2: logger.debug("register %s %s", self.tablename, item)
		r = self.dbtable.search(self.keyfield, '==', item.__dict__[self.keyfield])
		if r is None or len(r) == 0:
			item._wascreated = True
			self.dbtable.insert(item.save())
		else:
			item._wascreated = False
			item.load(r[0])
			logger.debug("register item loaded: %s", item)
		return item._wascreated
			

	def get_range(self, csk):
		drange =  self.dbtable.get_range(csk)
		if len(drange) == 0:
			return []
		ret = []
		keys = []
		to_load = csk.count
		if logging.DBG > 1: logger.debug("get_range drange len %s %s", len(drange), drange)
		for rkey in drange:
			r = drange[rkey]
			keys.append(r[csk.key_field])
			if r[self.keyfield] in self.cache:
				rn = self.cache[r[self.keyfield]]
			else:
				rn = self.itemclass()
				rn.load(r)
				self.cache[r[self.keyfield]] = rn
			ret.append(rn)
			to_load -= 1
			if to_load == 0:
				break
		csk.at_start = keys[0] == list(drange.keys())[0]
		csk.at_end = keys[-1] == list(drange.keys())[-1]
		csk.start_key = keys[0]
		csk.count = min(len(keys), csk.count)

		logger.debug("get_range found %s recs %s", len(ret), str(csk))
		return ret

	def find(self, key, keyfield = None):
		if keyfield is None:
			keyfield = self.keyfield
		res = self.dbtable.search(keyfield, '==', key)
		if logging.DBG > 2: logger.debug("find %s %s=%s: found %s", self.tablename, keyfield, key,  len(res))
		ret = []
		for r in res:
			if logging.DBG > 2: logger.debug("find->load %s", type(r))
			if r[self.keyfield] in self.cache:
				rn = self.cache[r[self.keyfield]]
			else:
				rn = self.itemclass()
				rn.load(r)
				self.cache[r[self.keyfield]] = rn
			ret.append(rn)
		return ret


	def find_one(self, key, keyfield = None):
		res = self.find(key, keyfield)
		if logging.DBG > 1: logger.debug("find_one %s %s: %s", self.tablename, key,  res)
		if len(res) == 1:
			return res[0]
		return None


	def update(self, item):
		self.dbtable.update(item.save(), self.keyfield, item.__dict__[self.keyfield])
		super().update(item)


	def insert(self, item):
		if logging.DBG > 2: logger.debug("Table insert %s  %s", item.save(), self.keyfield)
		self.dbtable.upsert(item.save(), self.keyfield)
		super().insert(item)


	def delete(self, item):
		super().delete(item)
		self.dbtable.delete(item.save(), self.keyfield)


	def unregister(self, item):
		self.dbtable.remove(self.keyfield, item.__dict__[self.keyfield])

	def __len__(self):
		return len(self.dbtable)

	def __str__(self):
		return "Dbndex(%s)%s" % (self.itemclass.__name__, len(self.dbtable))



class DictTable(Table):
	""" dict based Table """

	def __init__(self, itemclass,  keyfield, index):
		self.index = index
		super().__init__(itemclass, keyfield)


	def register(self, item):
		self.index[item.key] = item


	def get_range(self, csk):
		raise Exception("NotYetImplemented")

	def find(self, key, keyfield = None):
		if keyfield is None or keyfield == self.keyfield:	# native key searc
			try:
				return self.index[key]
			except:
				return None
		res = []
		for k in self.index.keys():
			if self.index[k][keyfield] == value:
				res.append(self.index[k])
		return res

	def update(self, item):
		pass


	def insert(self, item):
		if logging.DBG > 2: logger.debug("DictTable  insert %s", item.save())
		pass


	def delete(self, item):
		pass


	def unregister(self, item):
		if item.key in self.index:
			del self.index[item.key]


	def __str__(self):
		return "DictTable(%s)%s" %(self.itemclass.__name__, len(self.index))

#
# ItemLink
#
class ItemLink:
	""" provide link to related classed """
	def __init__(self, keyfield, link_class, link_field):
		self.keyfield = keyfield
		self.link_class = link_class
		self.link_field = link_field

	def get(self, rec):
		k = rec.__dict__[self.keyfield]
		logger.debug("ItemLink.get: keyfield %s key %s", self.keyfield, k)
		res = self.link_class._table.find(k, self.link_field)
		return res

	def get_linked_table_name(self):
		return self.link_class.__name__


#
# BaseItem
#
class BaseItem:
	def __init__(self, key):
		self._key = key


	def __del__(self):
		if logging.DBG > 2: logger.debug("BaseItem: __del__ %s", self)


	def __str__(self):
		try:
			return "%s (%s)" % (self._itype, self._key)
		except AttributeError:
			return "SomeBaseItem"



#
# Item
#
class Item(BaseItem):
	_table = None
	_parent_link = None

	def __init__(self, key):
		super().__init__(key)
		self._table = self.__class__._table
		self._itype = self.__class__.__name__
		if self._key is None:
			if logging.DBG > 1: logger.debug("Item: created %s", self)
		else:
			self._table.register(self)
			if logging.DBG > 1: logger.debug("Item: created and registered %s", self)


	def schedule_update(self, sid, stream_tag):
		if logging.DBG >= 0: logger.debug("Item %s: schedule_update %s %s", self._key, sid, stream_tag)
		pass


	def load(self, data):	#N.B.
		""" load class variable from provided data """
		if logging.DBG > 2: logger.debug("%s loading data: %s", self.itype, data)
		for k in data:
			self.__dict__[k] = data[k]
		self._key = self.__dict__[self._table.keyfield]


	def save(self):
		""" return dict of all non-private class variables """
		r = {}
		for k in self.__dict__:
			if k[0] == '_':
				continue
			r[k] = self.__dict__[k]
		return r

	def insert(self):
		if logging.DBG > 2: logger.debug("Item  insert %s %s", self._table.tablename, self.save())
		self._table.insert(self)

	def update(self):
		self._table.update(self)

	def delete(self):
		self._table.delete(self)

	def get_parent(self):
		if self.__class__._parent_link is None:
			return None
		res = self.__class__._parent_link.get(self)
		if len(res) > 1:
			logger.error("get_parent %s found more than one parent %s", self.__class__.__name__, res)
		elif len(res) == 0:
			logger.error("get_parent %s found no parent %s", self.__class__.__name__, res)
			return None
		return res[0]

	def gen_console_data(self):
		return self.save()


# LogQ
#
class LogQ:
	def __init__(self, conf, loop = None):
		self.conf = conf
		self.name = "logq"
		self.q = Queue(loop=loop)
		self.loop = loop

	def write(self, msg):
		asyncio.ensure_future(self.awrite(msg), loop=self.loop)
	
	async def awrite(self, msg):
		msg = msg.rstrip('\n')
		if len(msg) > 0:
			await self.q.put(msg)

	def flush(self):
		pass

	async def start(self):
		logger.info("%s logq start")
		while True:
			msg = await self.q.get()
			if msg is None:
				break
			if len(msg) == 0:
				continue
#			print("got one", msg)
#			emit to web consoles!!


