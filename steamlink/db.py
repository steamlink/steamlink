
# python library Steamlink 

import asyncio
import os
from tinydb import TinyDB, Query, Storage, where
from tinydb.database import  Document
from tinydb.storages import JSONStorage, touch
from tinydb.storages import MemoryStorage
from tinydb.middlewares import CachingMiddleware
from tinydb_smartcache import SmartCacheTable

import logging
logger = logging.getLogger()

import yaml
def represent_doc(dumper, data):
	# Represent `Document` objects as their dict's string representation
	# which PyYAML understands
	return dumper.represent_data(dict(data))


notyet = """
class SLDoc(dict):
	def __init__(self, value, doc_id, **kwargs):
		super().__init__(value, doc_id, **kwargs)
		pass

		self.update(value)
		self.doc_id = doc_id


"""
yaml.add_representer(Document, represent_doc)

class YAMLStorage(Storage):
	def __init__(self, path, create_dirs=False, **kwargs):
		super().__init__()
		touch(path, create_dirs=create_dirs)
		self.kwargs = kwargs
		self._handle = open(path, 'r+')


	def read(self):
		self._handle.seek(0, os.SEEK_END)
		size = self._handle.tell()
		if not size:
			# File is empty
			return None
		else:
			self._handle.seek(0)
			return yaml.safe_load(self._handle.read()) 


	def write(self, data):
		self._handle.seek(0)
		serialized = yaml.dump(data)
		self._handle.write(serialized)
		self._handle.flush()
		self._handle.truncate()


	def close(self): # (4) pass
		self._handle.close()
		pass
 

class DBTable:
	def __init__(self, table, name):
		if logging.DBG > 2: logger.debug("DBTable %s", name)
		self.table = table
		self.name = name
		self.query = Query()


	def insert(self, rec):
		did = self.table.insert(rec)
		if logging.DBG > 2: logger.debug("insert %s rec %s, %s: %s", self.name, did, type(rec), rec)


	def upsert(self, keyfield, rec):
		try:
			r = rec[keyfield]
		except:
			logger.error("insert with no '%s' field: %s", keyfield, rec)
			return
		el = self.table.search(where(keyfield) == r)
		logger.debug("upsert search %s return %s", (where(keyfield) == r), el)
		if el is not None and len(el) > 0:
			if el[0] == rec:
				logger.debug("upsert --nochange--")
				return
			did = self.table.update(rec, where(keyfield) == r)
			if logging.DBG >= 0: logger.debug("upsert update %s rec %s, %s: %s", self.name, did, type(rec), rec)
		else:
			did = self.table.insert(rec)
			if logging.DBG >= 0: logger.debug("upsert insert %s rec %s, %s: %s", self.name, did, type(rec), rec)


	def remove(self, field, val):
		el = self.table.get(where(field) == val)
		if el == None:
			logger.error("remove in %s, no document with %s=%s", self.name, field, key)
			return
		
		try:
			self.table.remove(eids=[el.eid])
		except KeyError as e:
			logger.error("remove in %s, no docid %s", self.name, el.eid)


	def search(self, field, op, val):
		q = "self.table.search(where('%s') %s %s)" % (field, op, repr(val))
		res = eval(q)
		if logging.DBG > 2: logger.debug("search %s rec %s: %s", self.name, q, res)
		return res


	def get_range(self, field, startv, endv, count=5):
		if startv == None:
			startv = 0
		if endv == None:
			r0 = self.table.search(where(field) >= startv)
			if len(r0) == 0:
				return (None, None, None)
			ulist = []
			for x in r0:
				ulist.append(x[field])
			slist = sorted(ulist)
			print('r0', slist, len(slist))
			count = min(len(r0), count)
			endv = slist[count-1]
		if startv > endv:
			return (None, None, None)

		r1 = self.table.search((where(field) >= startv) & (where(field) <= endv))
		if len(r1) == 0:
			return (None, None, None)
		ulist = []
		for x in r1:
			ulist.append(x[field])
		slist = sorted(ulist)
		print("r1", slist)
		startv = slist[0]
		count = min(len(slist), count)
		endv = slist[count-1]
		return (startv, endv, count)


	def __len__(self):
		return len(self.table)



class DB:
	""" Notes:
			- inserts get slow with the standard Json modules, check out ujson
			    ( at 3000 items, the per item insert time is 12ms!! )
			- consider creating a new table/db/fle very day or every x records
			 
	"""
	def __init__(self, conf, loop):
		if logging.DBG > 2: logger.debug("DB %s", conf)
		self.name = "DB"
		self.conf = conf
		self.loop = loop
		self.db = None
		self.db_tables = {}


	async def start(self):
		TinyDB.table_class = SmartCacheTable

		logger.info("opening DB %s", self.conf['db_filename'])
		self.db = TinyDB(self.conf['db_filename'], \
				storage=CachingMiddleware(YAMLStorage))
#		self.db = TinyDB(self.conf['db_filename'])


	def table(self, name):
		if name in self.db_tables:
			return self.db_tables[name]
	
		db_table = self.db.table(name)
		table = DBTable(db_table, name)
		self.db_tables[name] = table
		return table


	async def stop(self):
		if logging.DBG > 2: logger.debug("closing db")
		self.close()


	def close(self):
		for tab in self.db_tables:
			try:
				self.db_tables[tab].close()
				del self.db_tables[tab]
			except:
				pass
		self.db.close()
		self.db = None




