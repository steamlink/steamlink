
# python library Steamlink 

import asyncio
import os
from collections import  Mapping, OrderedDict

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
		if logging.DBG >= 2: logger.debug("REC insert %s rec %s, %s: %s", self.name, did, type(rec), rec)


	def upsert(self, rec, keyfield):
		try:
			r = rec[keyfield]
		except:
			logger.error("insert with no '%s' field: %s", keyfield, rec)
			return
		el = self.table.search(where(keyfield) == r)
		if logging.DBG > 2: logger.debug("upsert search %s return %s", (where(keyfield) == r), el)
		if el is not None and len(el) > 0:
			if el[0] == rec:
				if logging.DBG > 2: logger.debug("upsert --nochange--")
				return
			did = self.table.update(rec, where(keyfield) == r)
			if logging.DBG >= 2: logger.debug("REC upsert update %s rec %s, %s: %s", self.name, did, type(rec), rec)
		else:
			did = self.table.insert(rec)
			if logging.DBG >= 2: logger.debug("upsert insert %s rec %s, %s: %s", self.name, did, type(rec), rec)


	def update(self, rec, keyfield, key):
		if logging.DBG >= 2: logger.debug("REC update %s rec %s", self.name, rec)
		did = self.table.update(rec, where(keyfield) == key)

	def delete(self, field, val):
		el = self.table.get(where(field) == val)
		if el is None:
			logger.error("delete in %s, no document with %s=%s", self.name, field, key)
			return
		
		try:
			self.table.remove(eids=[el.eid])
		except KeyError as e:
			logger.error("delete in %s, no docid %s", self.name, el.eid)


	def search(self, field, op, val):
		q = "self.table.search(where('%s') %s %s)" % (field, op, repr(val))
		res = eval(q)
		if logging.DBG > 2: logger.debug("search %s rec %s: %s", self.name, q, res)
		return res


	def get_range(self, field, startv, endv, count=5):
		if logging.DBG > 1: logger.debug("get_range: field '%s' startv '%s' endv '%s' count '%s'", \
				field, startv, endv, count)

		tab = self.table.all()
		if len(tab) == 0:
			return []

		udict = {}
		for t in tab:
			udict[t[field]] = t
		sdict = sorted(udict)
		
		if startv in [None, '', '<START>']:
			sidx = 0
			startv = sdict[sidx]
		elif startv in [-1, '<END>']:
			sidx = min(len(sdict)-1, count-1)
			startv = sdict[sdix]
		else:
			sidx = None
			for idx in range(len(sdict)):
				if sdict[idx] >=  startv:
					sidx = idx
					startv = sdict[sidx]
					break
			if sidx is None:
				return []
		if endv in [None, '', -1]:
			eidx = len(sdict)-1
			endv = sdict[eidx]
		else:
			eidx = None
			for idx in range(sidx, len(sdict)):
				print("BBBB checking  %s >= %s", sdict[idx], startv)
				if sdict[idx] < endv:
					endv = sdict[idx]
					eidx = idx
					break
			if eidx is None:
				return []
			
		res = OrderedDict()
		for idx in range(sidx, eidx+1):
			res[sdict[idx]]  = udict[sdict[idx]]
		if logging.DBG > 1: logger.debug("get_range res=%s", res)
		return res


	def Oldget_range(self, field, startv, endv, count=5):
		logger.debug("get_range: field '%s' startv '%s' endv '%s' count '%s'", \
				field, startv, endv, count)
		if startv in [None, '']:
			r0 = self.table.all()
			if len(r0) > 0:
				startv = r0[0][field]
			else:
				return []
			print("ZZZZZ startv ", startv)
		if endv is None:
			r0 = self.table.search(where(field) >= startv)
			if len(r0) == 0:
				return []
			ulist = []
			for x in r0:
				ulist.append(x[field])
			slist = sorted(ulist)
			count = min(len(r0), count)
			endv = slist[count-1]
			print("ZZZZZ endv ", endv)
		if startv > endv:
			return []

		return  self.table.search((where(field) >= startv) & (where(field) <= endv))


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
#		TinyDB.table_class = SmartCacheTable

		logger.info("opening DB %s", self.conf['db_filename'])
		self.db = TinyDB(self.conf['db_filename'], \
				sort_keys=True, indent=4, separators=(',', ': '), \
#				storage=CachingMiddleware(YAMLStorage))
				storage=CachingMiddleware(JSONStorage))
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


	def flush(self):
		self.db._storage.flush()


