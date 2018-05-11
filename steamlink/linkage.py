
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



def check_restrictions( restrict_by, item):
	if len(restrict_by) == 0:
		return True
	res = True
	for restrict in restrict_by:
		field =  restrict['field_name'] 
		op =  restrict['op'] 
		value =  restrict['value'] 
		ex = "item['%s'] %s %s" % (field, op, repr(value))
		res = res and eval(ex)
	return res


import yaml
from yaml import Loader, Dumper

class CSearchKey:
	def __init__(self, table_name, key_field, start_key, start_item_number, count, stream_tag, end_key = None, restrict_by = []):

		self.table_name = table_name
		self.key_field = key_field
		if key_field == 'ts' and start_key is not None:					# to help javascript...
			self.start_key = float(start_key)
		else:
			self.start_key = start_key
		self.start_item_number = start_item_number
		self.end_key = end_key
		self.count = count
		self.stream_tag = stream_tag

		self.at_start = False
		self.at_end = False
		self.total_item_count = 0

		self.restrict_by = restrict_by
		self.search_id =  self.__repr__()	#used to index CSearches 


	def __repr__(self):
		return  "%s(%s:%s:%s)_%s_%s_%s" %\
			(self.table_name, self.key_field, self.start_key, self.end_key,  \
			self.restrict_by, self.start_item_number, self.stream_tag)

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
		if 'csearch' in logging.DBGK: logger.debug("CSearch csearch key: %s", str(csearchkey))


	def __str__(self):
		return "CSearch[%s]" % self.search_id


	def add_item(self, item):
		if 'csearch' in logging.DBGK: logging.debug("CSearch '%s' add_item %s in key %s", self.search_id, item, self.csearchkey.key_field)
		self.cs_items[item.__dict__[self.csearchkey.key_field]] = CSearchItem(self, item)


	def drop_item(self, item):
		if 'csearch' in logging.DBGK: logger.debug("CSearch '%s' drop_item %s", self.search_id, item)
		self.cs_items[item.__dict__[self.csearchkey.key_field]].deleted = True
#?		del self.cs_items[item.__dict__[self.csearchkey.key_field]]


	def add_sid(self, sid):
		if 'csearch' in logging.DBGK: logger.debug("CSearch '%s' add_sid %s csearchkey %s", self.search_id, sid, self.csearchkey)
		self.clients[sid] = self.csearchkey.stream_tag
		self.webnamespace.enter_room(sid, self.search_id, \
			 self.webnamespace.namespace)
		return self.csearchkey


	def drop_sid(self, sid):
		if sid in self.clients:
			if 'csearch' in logging.DBGK: logger.debug("CSearch '%s' drop_sid %s", self.search_id, sid)
			del self.clients[sid]
			self.webnamespace.leave_room(sid, self.search_id, \
				 self.webnamespace.namespace)


	def num_clients(self):
		return len(self.clients)


	def check_csearch(self, op, item, force = False):
		""" check if item matches any registered searches, 
			schedule update for all search clients it matched 
			op is 'ins', 'upd' or 'del'
			note that item is updated or inserted in db before check_csearch
			but the delete will happen after
		"""
# find csitem for item
		if 'csearch' in logging.DBGK: logger.debug("check_csearch (CSearch) %s force=%s op=%s item=%s",\
				 self, force, op, item)
		item_search_key = item.__dict__[self.csearchkey.key_field]
		push = False
		go = check_restrictions(self.csearchkey.restrict_by, item.__dict__)
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

		if push or force:
			if 'csearch' in logging.DBGK: logger.debug("check_csearch (push) %s force=%s", self, force)
			try:
				self.cs_items[item_search_key].push_update(force)
			except KeyError as e:
				logger.error("check_csearch (push) keyerror: %s", e)
		return 


	def force_update(self, sid):
		if not sid in self.clients:
			return
		if 'csearch' in logging.DBGK: logger.debug("force_update ")
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
		self.upd_in_progress = False


#	def __getstate__(self):
#		r = self.__dict__.copy()
#		r['csearch'] = self.csearch.csearchkey 
#		r['item'] = self.item.key
#		return r

	def __repr__(self):
		return str("CSI %s %s" % (self.csearch.csearchkey, self.item))

	def console_update(self, force):
		if 'csearch' in logging.DBGK: logger.debug("console_update %s %s", self, force)
		data_to_emit = self.item.gen_console_data()
		if self.deleted:
			data_to_emit['_del_key'] = 1

		if 'csearch' in logging.DBGK: logger.debug("console_update CSearch %s Item %s Data %s", self.csearch, self.item, str(data_to_emit)[:32]+"...")
		return data_to_emit


	async def schedule_future_update(self, wait):
		await asyncio.sleep(wait)
		self.future_update = False
		self.push_update(False)


	def push_update(self, force, sroom = None):
		if self.upd_in_progress or _WEBAPP is None:
			return
		next_update = (self.last_update + _WEBAPP.minupdinterval) - _WEBAPP.loop.time()
		if 'csearch' in logging.DBGK: logger.debug("push_update %s %s %s next: %s", self, force, sroom, next_update)
		if not force and next_update > 0:
			if not self.future_update:
				asyncio.ensure_future(self.schedule_future_update(next_update))
				self.future_update = True
			return

		if 'csearch' in logging.DBGK: logger.debug("push_update %s %s %s next: %s", self, force, sroom, next_update)
		self.upd_in_progress = True
		_WEBAPP.queue_item_update(self, force)


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
		self.warned = False
		self.gets = 0
		self.sets = 0
		self.hits = 0
		self.misses = 0
		self.replaces = 0
		if 'ocache' in logging.DBGK: logger.debug("OCache %s created", self.tablename)

	def __del__(self):
		if 'ocache' in logging.DBGK: logger.debug("OCache %s destroyed", self.tablename)
		return super().__del__()

	def __getitem__(self, key):
		self.gets += 1
		self.ts[key] = time.time()
		if 'ocache' in logging.DBGK: logger.debug("OCache %s __getitem %s", self.tablename, key)
		return super().__getitem__(key)


	def __delitem__(self, key):
		if 'ocache' in logging.DBGK: logger.debug("OCache %s __delitem %s", self.tablename, key)
		return super().__delitem__(key)


	def has(self, key):
		if key in self:
			self.hits += 1
			return True
		self.misses += 1
		return False

	def __setitem__(self, key, value):
		if key in self:
			self.replaces += 1
		super().__setitem__(key, value)
		self.sets += 1
		self.ts[key] = time.time()
		if 'ocache' in logging.DBGK: logger.debug("OCache %s __setitem__ %s %s at %s", self.tablename, key, value, self.ts[key])
		if len(self) > self.max_entries:
			self.clean()
		else:
			self.warned = False


	def clean(self):
		if 'ocache' in logging.DBGK: logger.debug("OCache %s clean!", self.tablename)
#		to_prune =  len(self) - self.max_entries
		to_prune =  int(self.max_entries / 10)
		for p in range(to_prune):
			ts = time.time()
			d = None
			for i in self.keys():
				if sys.getrefcount(super().__getitem__(i)) == 2:	# N.B sys.getrefcount and  self[i]
					if ts > self.ts[i]:
						d = i
						ts = self.ts[i]
			if d is None:
				break
			del self[d]
			del self.ts[d]

		if len(self) >= self.max_entries:
				self.warned = True
				logger.warning("cache overcommited: "+self.status())

		if 'ocache' in logging.DBGK: logger.debug("OCache cleaned: %s", self.status())
		return 

	def status(self):
		in_use = 0
		occupied = len(self)
		for i in self.keys():
			if sys.getrefcount(super().__getitem__(i)) != 2:
				in_use += 1
		return "'%s' %s in use, %s of %s ocupied (%s%%) m %s h %s s %s g %s r %s" % \
			(self.tablename, in_use, occupied, self.max_entries, (100.0 * occupied / self.max_entries),
				self.misses, self.hits, self.sets, self.gets, self.replaces)


#
# Table
#
class Table:
	tables = {}

	""" provide an index over items """
	index = None
	def __init__(self, itemclass, keyfield):
		self.itemclass = itemclass
		self.keyfield = keyfield
		self.csearches = {}
		self.sid_stream_tags = {}
		Table.tables[self.itemclass.__name__] = self


	def add_csearch(self, webnamespace, csearchkey, sid):
		""" add a cserarch for sid to this table """

		self.drop_stream_tag_from_csearch(sid, csearchkey.stream_tag)
		srch_id = csearchkey.search_id
		if not srch_id in self.csearches:
			if 'webupd' in logging.DBGK: logger.debug("table add_csearch '%s' new: %s", srch_id, csearchkey)
			self.csearches[srch_id] = CSearch(webnamespace, self, csearchkey)
		else:
			if 'webupd' in logging.DBGK: logger.debug("table add_csearch search_id '%s' from cache", srch_id)
		csearchkey = self.csearches[srch_id].add_sid(sid)
		return csearchkey


	def drop_stream_tag_from_csearch(self, sid, stream_tag):
		""" delete sid from any csearches with the same stream_tag """
		del_client = []
		for cs in self.csearches:
			if sid in self.csearches[cs].clients \
				and stream_tag == self.csearches[cs].csearchkey.stream_tag:
				del_client.append(cs)

		for dc in  del_client:
			if 'webupd' in logging.DBGK: logger.debug("table drop_stream_tag_from_csearch t %s", dc)
			del self.csearches[dc].clients[sid]
			if len(self.csearches[dc].clients) == 0:
				del self.csearches[dc]


	def drop_sid_from_csearch(self, sid):
		""" delete sid from all csearches """
		del_list = []
		for cs in self.csearches:
			self.csearches[cs].drop_sid(sid)
			if 'webupd' in logging.DBGK: logger.debug("table drop_sid_from_csearch '%s'", sid)
			if self.csearches[cs].num_clients() == 0:
				del_list.append(cs)
		for cs in del_list:
			if 'webupd' in logging.DBGK: logger.debug("table drop_sid_from_csearch csearch empty! '%s'", cs)
			del self.csearches[cs]

	def register(self, item):
		""" either load item from backing store, if it exists, or store it there """
		pass


	def find(self, key, keyfield = None):
		pass

	def find_one(self, key, keyfield = None):
		pass

	def insert(self, item):
		self.check_csearch('ins', item, force=False)

	def db_update(self, item, force):
		self.check_csearch('upd', item, force)

	def delete(self, item):
		self.check_csearch('del', item, force=False)

	def check_csearch(self, op, item, force):
		for cs in self.csearches:
			if 'webupd' in logging.DBGK: logger.debug("check_csearch (Table) %s force=%s", cs, force)
			self.csearches[cs].check_csearch(op, item, force)
		 
	
class DbTable(Table):
	""" database based Table """

	def __init__(self, itemclass, keyfield, tablename):
		self.tablename = tablename
		self.cache = OCache(tablename, 1000)
		self.dbtable = _DB.table(self.tablename)
		super().__init__(itemclass, keyfield)
	

	def register(self, item):
		""" backload item from db if it exists, otherwise insert in db """
		if logging.DBG > 2: logger.debug("register %s %s", self.tablename, item)
		r = self.dbtable.search(self.keyfield, '==', item.__dict__[self.keyfield])
		if r is None or len(r) == 0:
			item._wascreated = True
			self.insert(item)
		else:
			item._wascreated = False
			item.load(r[0])
			self.cache[item.__dict__[self.keyfield]] = item
		super().register(item)
		return item._wascreated
			

	def db_to_class(self, drange):
		ret = []
		for rkey in drange:
			if logging.DBG > 2: logger.debug("find->load %s", type(r))
			r = drange[rkey]
			if self.cache.has(r[self.keyfield]):
				rn = self.cache[r[self.keyfield]]
			else:
				rn = self.itemclass()
				rn.load(r)
				self.cache[r[self.keyfield]] = rn
			ret.append(rn)
		return ret


	def get_range(self, csk):
		""" get a range of items, defined by a CSearch """
		drange =  self.dbtable.get_range(csk)
		if len(drange) == 0:
			return []
		if logging.DBG > 1: logger.debug("get_range drange len %s %s", len(drange), drange)
		return self.db_to_class(drange)


	def find(self, key, keyfield = None):
		if keyfield is None:
			keyfield = self.keyfield
		res = self.dbtable.search(keyfield, '==', key)
		if logging.DBG > 2: logger.debug("find %s %s=%s: found %s", self.tablename, keyfield, key,  len(res))
		ret = []
		for r in res:
			if logging.DBG > 2: logger.debug("find->load %s", type(r))
			if self.cache.has(r[self.keyfield]):
				rn = self.cache[r[self.keyfield]]
			else:
				rn = self.itemclass()
				rn.load(r)
				self.cache[r[self.keyfield]] = rn
			ret.append(rn)
		return ret


	def find_one(self, key, keyfield = None):
		""" find exactly one item with keyfield key """
		if keyfield is None:
			keyfield = self.keyfield
		if keyfield == self.keyfield:	# i.e. native key
			if self.cache.has(key):
				return self.cache[key]
		res = self.find(key, keyfield)
		if logging.DBG > 1: logger.debug("find_one %s %s: %s", self.tablename, key,  res)
		if len(res) == 1:
			return res[0]
		elif len(res) == 0:
			return None
		logger.error("multiple items with '%s' = %s in table %s", keyfield, key, self.tablename)
		return None


	def db_update(self, item, force=False):
		if 'webupd' in logging.DBGK: logger.debug("db_update (DBTable) %s force=%s", self, force)
		self.dbtable.db_update(item.save(), self.keyfield, item.__dict__[self.keyfield])
		self.cache[item.__dict__[self.keyfield]] = item
		super().db_update(item, force)
		

	def insert(self, item):
		if logging.DBG > 2: logger.debug("Table insert %s  %s", item.save(), self.keyfield)
		self.dbtable.upsert(item.save(), self.keyfield)
		self.cache[item.__dict__[self.keyfield]] = item
		super().insert(item)


	def delete(self, item):
		del self.cache[item.__dict__[self.keyfield]]
		self.dbtable.delete(item.save(), self.keyfield)		# ?? order
		super().delete(item)


	def __len__(self):
		return len(self.dbtable)

	def __str__(self):
		return "Dbindex(%s)%s" % (self.itemclass.__name__, len(self.dbtable))



class DictTable(Table):
	""" dict based Table """

	def __init__(self, itemclass,  keyfield, index):
		self.tablename = itemclass.__name__
		self.index = index
		super().__init__(itemclass, keyfield)


	def register(self, item):
		logger.debug("DictTable register %s %s", item.__dict__[self.keyfield], item)
		self.index[item.__dict__[self.keyfield]] = item


	def get_range(self, csk):
		if logging.DBG > 1: logger.debug("get_range csk %s", str(csk))
		field = csk.key_field
		startv = csk.start_key
		endv = csk.end_key
		count = csk.count

		csk.total_item_count = 0

		tab = self.index
		if len(tab) == 0:
			if logging.DBG > 1: logger.debug("get_range table empty")
			csk.count = 0
			return {}

		udict = {}
		for t in tab:
			logger.debug("DictTable get_range %s %s", field, t)
			
			udict[tab[t].__dict__[field]] = tab[t]
		fullsdict = sorted(udict)
		if logging.DBG > 1: logger.debug("get_range table %s items", len(fullsdict))
	
		if len(csk.restrict_by) == 0:
			sdict = fullsdict
		else:
			sdict = []
			for r in fullsdict:
				if check_restrictions(csk.restrict_by, udict[r]):
					sdict.append(r)

		if len(sdict) == 0:
			if logging.DBG > 1: logger.debug("get_range table empty after destrict")
			return {}

		if startv in [None]:
			if csk.start_item_number < 0:
				sidx = max(0, len(sdict) + csk.start_item_number)
			else:
				sidx = min(csk.start_item_number, len(sdict)-1)
			startv = sdict[sidx]
		else:
			sidx = None
			for idx in range(len(sdict)):
				if sdict[idx] >=  startv:
					sidx = idx
					startv = sdict[sidx]
					break
			if sidx is None:
				if logging.DBG > 1: logger.debug("get_range no start key found")
				return {}
		if endv in [None]:
			eidx = min(sidx + count-1, len(sdict)-1)
			endv = sdict[eidx]
		else:
			eidx = None
			for idx in range(sidx, len(sdict)):
				if sdict[idx] <= endv:
					endv = sdict[idx]
					eidx = idx
					break
			if eidx is None:
				if logging.DBG > 1: logger.debug("get_range no end key found")
				return {}
			count = eidx - sidx + 1
		res = {}
		for idx in range(sidx, eidx+1):
			res[sdict[idx]]  = udict[sdict[idx]]

		csk.start_key = sdict[sidx]
		csk.end_key = sdict[eidx]
		csk.start_item_number = sidx
		csk.total_item_count = len(sdict)
		csk.at_start = csk.start_key == sdict[0]
		csk.at_end = csk.end_key == sdict[-1]

		drange = []
		for r in res:
			drange.append(res[r])
		if logging.DBG > 1: logger.debug("dict get_range drange len %s %s", len(drange), drange)
		return drange


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

	def db_update(self, item, force=False):
		if 'webupd' in logging.DBGK: logger.debug("db_update (DictTable)  %s force=%s", self, force)
		super().db_update(item, force)


	def insert(self, item):
		if logging.DBG > 2: logger.debug("DictTable  insert %s", item.save())
		pass


	def delete(self, item):
		pass


	def __len__(self):
		return len(self.index)


	def __str__(self):
		return "DictTable(%s)%s" %(self.itemclass.__name__, len(self.index))

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

	def __init__(self, key):
		super().__init__(key)
		self._table = self.__class__._table
		self._itype = self.__class__.__name__
		if self._key is None:
			if logging.DBG >= 1: logger.debug("Item created for load()")
		else: 		# skip key-less items, they will come in via load()
			self._table.register(self)
			if logging.DBG >= 0: logger.debug("Item created and loaded: %s", self)


	def load(self, data):	#N.B.
		""" load class variable from provided data """
		if logging.DBG > 2: logger.debug("%s loading data: %s", self.itype, data)
		for k in data:
			self.__dict__[k] = data[k]
		self._key = self.__dict__[self._table.keyfield]
		logger.debug("Item loaded: %s", self)


	def save(self, withvirtual=False):
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

	def update(self, force=False):
		if 'webupd' in logging.DBGK: logger.debug("Item update %s force=%s", self, force)
		self._table.db_update(self, force)

	def delete(self):
		self._table.delete(self)

	def gen_console_data(self):
		return self.save(withvirtual=True)

#
# LogItem
class LogItem(Item):
#	console_fields = {
#		"Time": "time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.ts))",	
#		"lvl": "self.lvl",
#		"ts": "self.ts",
#		"line": "self.line",
#	}

	keyfield = "ts"
	def __init__(self, lvl = None, line = None):
		if lvl == None:
			self.ts = None
		else:
			self.ts = time.time()
		self.lvl = lvl
		self.line = line
		super().__init__(self.ts)

	def save(self, withvirtual=False):
		r = {}
		r["ts"] = self.ts
		r["lvl"] = self.lvl
		r["line"] = self.line
		if withvirtual:
			r['Time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.ts))
			if len(self.line) > 70:
				r["line"] = self.line[:70] + "..."
		return r

# LogQ
#
class LogQ(Item):
	def __init__(self, conf = None, loop = None):
		self.name = None
		if conf is not None:
			self.name = "logq"
			self.conf = conf
			self.q = Queue(loop=loop)
			self.loop = loop
		super().__init__(self.name)
		logger.info("%s logq init", self)

	def save(self, withvirtual=False):
		r = {}
		if withvirtual:
			for t in Table.tables:
				table = Table.tables[t]
				r[ table.tablename + " table"] = len(table)
				try:
					r[ table.tablename + " cache"] = table.cache.status()
				except:
					pass
		return r

	def write(self, msg):
		asyncio.ensure_future(self.awrite(msg), loop=self.loop)
	
	async def awrite(self, msg):
		msg = msg.rstrip('\n')
		if len(msg) > 0:
			await self.q.put(msg)

	def flush(self):
		pass

	async def start(self):
		LogItem._table = DbTable(LogItem, keyfield="ts", tablename="LogItem")
		logger.info("%s logq start", self)
		while True:
			msg = await self.q.get()
			if msg is None:
				break
			if len(msg) == 0:
				continue

			try:
				lvl, line = msg.split(None, 1)
				who, what = line.split(None,1)
				if who in ['asyncio_server','asyncio_socket','server','web_protocol']:	# don't log these
					continue
			except:
				continue		# loose badly formated log lines

			logitem = LogItem(lvl, line)
			if lvl in ['INFO', 'WARNING', 'ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY']:
				_WEBAPP.send_console_alert(lvl, line)				
		logger.info("%s logq stop", self)

