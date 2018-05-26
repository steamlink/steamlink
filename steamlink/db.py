# python library Steamlink

import bisect
import logging
from collections import OrderedDict

from tinydb import TinyDB, Query, where
from tinydb.middlewares import CachingMiddleware
from tinydb.storages import JSONStorage

from . import (DBG, DBGK)

logger = logging.getLogger()


#
# DBIndex
#
class DBIndex(OrderedDict):
	def __init__(self, table, csk):
		self.table = table
		self.csk = csk
		self.key_field = csk.key_field
		super().__init__()

		if 'dbops' in DBGK: logger.debug("DBIndex __init__ %s", csk)
		for item in self.table:
			if self.csk.check_restrictions(item):
				self[item[self.key_field]] = item
		if 'dbops' in DBGK: logger.debug("DBIndex __init__ count %s", len(self))


	def has(self, key):
		return key in self


	def db_update(self, item):  # N.B. handle change of key value
		key = item[self.key_field]
		if self.csk.check_restrictions(item):
			super().__setitem__(key, item)


	def db_insert(self, item):
		key = item[self.key_field]
		if self.csk.check_restrictions(item):
			super().__setitem__(key, item)


	def db_delete(self, item):
		if 'dbops' in DBGK: logger.debug("DBIndex  deleting item %s", item)
		key = item[self.key_field]
		if key in self:
			del self[key]


#
# DBIndexFarm
#
class DBIndexFarm(dict):
	def __init__(self, table):
		self.table = table
		super().__init__()


	@staticmethod
	def mk_restrict_idx_name(csk):
		name = ""
		for restrict in csk.restrict_by:
			name += "%s%s%s" % (restrict['field_name'], restrict['op'], restrict['value'])
		return name


	def get_idx(self, csk):
		key_field = csk.key_field
		restrict_name = key_field + self.mk_restrict_idx_name(csk)
		if 'dbops' in DBGK: logger.debug("DBIndexFarm get name '%s'", restrict_name)
		if restrict_name not in self:
			self[restrict_name] = DBIndex(self.table, csk)
		return self[restrict_name]


	def db_update(self, item):
		for idx in self:
			self[idx].db_update(item)


	def db_insert(self, item):
		for idx in self:
			self[idx].db_insert(item)


	def db_delete(self, item):
		if 'dbops' in DBGK: logger.debug("DBIndexFarm  deleting item %s", item)
		for idx in self:
			self[idx].db_delete(item)


#
# DBTable
#
class DBTable:
	def __init__(self, table, name, key_field):
		if DBG > 2: logger.debug("DBTable %s", name)
		self.table = table
		self.name = name
		self.key_field = key_field  # field that is unique for this table
		self.restrict_idxs = DBIndexFarm(self.table)
		self.query = Query()


	def db_insert(self, rec):
		assert self.key_field in rec, "record has not key_field"
		r = rec[self.key_field]
		el = self.table.search(where(self.key_field) == r)
		if 'dbops' in DBGK: logger.debug("upsert search %s return %s", (where(self.key_field) == r), el)
		if el is not None and len(el) > 0:
			logger.error("duplicate record %s, %s rec %s, %s", self.name, el, r, rec)
			return
		did = self.table.insert(rec)
		if 'dbops' in DBGK: logger.debug("upsert insert %s rec %s, %s: %s", self.name, did, type(rec), rec)
		self.restrict_idxs.db_insert(rec)


	def db_update(self, rec):
		if 'dbops' in DBGK: logger.debug("REC update %s rec %s", self.name, rec)
		self.restrict_idxs.db_update(rec)
		key = rec[self.key_field]
		self.table.update(rec, where(self.key_field) == key)


	def db_delete(self, rec):
		field = self.key_field
		val = rec[field]
		if 'dbops' in DBGK: logger.debug("DBtable  deleting field=%s val=%s", field, val)
		self.restrict_idxs.db_delete(rec)
		el = self.table.get(where(field) == val)
		if el is None:
			logger.error("delete in %s, no document with %s=%s", self.name, field, val)
			raise ValueError

		try:
			self.table.remove(eids=[el.eid])
		except KeyError as e:
			logger.error("delete in %s, no docid %s", self.name, el.eid)


	def get(self, field, op, val):
		q = "self.table.get(where('%s') %s %s)" % (field, op, repr(val))
		res = eval(q)
		if 'dbops' in DBGK: logger.debug("get %s rec %s: %s", self.name, q, res)
		return res


	def search(self, field, op, val):
		q = "self.table.search(where('%s') %s %s)" % (field, op, repr(val))
		res = eval(q)
		if 'dbops' in DBGK: logger.debug("search %s rec %s: %s", self.name, q, res)
		return res


	def get_range(self, csk):
		""" get a range of records, obeying restrictions
		- if start_key is null, use start_item_number.
		- if start_item_number is negative start from the end
		if 'get_range' in DBGK: logger.debug("get_range: %s", str(csk))
		"""
		if 'get_range' in DBGK: logger.debug("get_range csk %s", str(csk))
		key_field = csk.key_field
		startv = csk.start_key
		endv = csk.end_key
		count = csk.count

		csk.total_item_count = 0

		if 'get_range' in DBGK: logger.debug("get_range csk2 %s", str(csk))
		if False:
			#		if len(self.table) == 0:
			if 'get_range' in DBGK: logger.debug("get_range table empty")
			csk.count = 0
			return {}

		if 'get_range' in DBGK: logger.debug("get_range num idexes %s", len(self.restrict_idxs))

		idx = self.restrict_idxs.get_idx(csk)

		if len(idx) == 0:
			if 'get_range' in DBGK: logger.debug("get_range table empty after destrict")
			return {}

		if startv in [None]:
			if csk.start_item_number < 0:
				sidx = max(0, len(idx) + csk.start_item_number)
			else:
				sidx = min(csk.start_item_number, len(idx) - 1)
			startv = list(idx)[sidx]
		else:
			sidx = bisect.bisect_left(list(idx), startv)
			if sidx == len(idx):
				if 'get_range' in DBGK: logger.debug("get_range no start key found")
				return {}

		if endv in [None]:
			eidx = min(sidx + count - 1, len(idx) - 1)
			endv = list(idx)[eidx]
		else:
			eidx = bisect.bisect_right(list(idx), endv, sidx, len(idx)) - 1
			if eidx < 0:
				if 'get_range' in DBGK: logger.debug("get_range no end key found")
				return {}
			count = eidx - sidx + 1

		csk.start_key = list(idx)[sidx]
		csk.end_key = list(idx)[eidx]
		csk.start_item_number = sidx
		csk.count = count
		csk.total_item_count = len(idx)
		csk.at_start = csk.start_key == list(idx)[0]
		csk.at_end = csk.end_key == list(idx)[-1]

		if 'get_range' in DBGK: logger.debug("get_range size %s", (eidx - sidx + 1))
		for i in range(sidx, eidx + 1):
			yield idx[list(idx)[i]]


	def __len__(self):
		return len(self.table)


class DB:
	""" Notes:
			- inserts get slow with the standard Json modules, check out ujson
			    ( at 3000 items, the per item insert time is 12ms!! )
			- consider creating a new table/db/fle very day or every x records

	"""


	def __init__(self, conf, loop):
		if DBG > 2: logger.debug("DB %s", conf)
		self.name = "DB"
		self.conf = conf
		self.loop = loop
		self.db = None
		self.db_tables = {}


	async def start(self):

		logger.info("%s opening DB %s", self.name, self.conf['db_filename'])
		self.db = TinyDB(self.conf['db_filename'],
						 sort_keys=True, indent=4, separators=(',', ': '),
						 storage=CachingMiddleware(JSONStorage))


	def table(self, name, key_field):
		if name in self.db_tables:
			return self.db_tables[name]

		db_table = self.db.table(name)
		table = DBTable(db_table, name, key_field)
		self.db_tables[name] = table
		return table


	async def stop(self):
		self.close()


	def close(self):
		logger.info("%s closing DB", self.name)
		for tab in self.db_tables:
			try:
				self.db_tables[tab].close()
				del self.db_tables[tab]
			except:
				pass
		self.db.close()
		self.db = None


	def flush(self):
		self.db._storage.flush()
