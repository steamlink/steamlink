
import asyncio
from tinydb import TinyDB, Query
from tinydb.storages import JSONStorage
from tinydb.storages import MemoryStorage
from tinydb.middlewares import CachingMiddleware


import logging
logger = logging.getLogger()


class DB:
	def __init__(self, conf, loop):
		self.name = "DB"
		self.conf = conf
		self.loop = loop
		self.db = None

	async def start(self):
		logger.info("opening DB %s", self.conf['db_filename'])
		self.db = TinyDB(self.conf['db_filename'], \
				sort_keys=True, indent=4, separators=(',', ': '), \
				storage=CachingMiddleware(JSONStorage))
		self.table = self.db.table('pkt')

	async def stop(self):
		logger.debug("closing db")
		self.db.close()


	def insert(self, rec):
		did = self.table.insert(rec)
		logger.debug("insert rec %s: %s", did, rec)
