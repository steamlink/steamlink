
import asyncio
from tinydb import TinyDB, Query

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
		self.db = TinyDB(self.conf['db_filename'])
		self.table = self.db.table('pkt')


	def insert(self, rec):
		did = self.table.insert(rec)
		logger.debug("insert rec %s: %s", did, rec)
