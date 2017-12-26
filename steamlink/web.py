
import asyncio
import socketio
import signal

from aiohttp import web
from aiohttp.log import access_logger, web_logger

from steamlink.steamlink import (
	Room,
	Steam,
	Mesh,
	Node,
	SL_OP,
	Packet,
	LogData,
	registry,
)


from steamlink.const import (
	LIB_DIR,
#	PROJECT_PACKAGE_NAME, 
	INDEX_HTML, 
#	__version__
)

import logging
logger = logging.getLogger(__name__)

from yarl import URL


class GracefulExit(SystemExit):
	code = 1


def raise_graceful_exit():
	raise GracefulExit()



class SLConsoleNamespace(socketio.AsyncNamespace):
	def __init__(self, webapp):
		self.webapp = webapp
		self.namespace = self.webapp.namespace

		super().__init__(self.namespace)
		logger.debug("SLConsoleNamespace registered for namespace %s", self.namespace)


	def on_connect(self, sid, environ):
		logger.debug("SLConsoleNamespace connect %s",str(environ['REMOTE_ADDR']))

	def on_disconnect(self, sid):
		logger.debug("SLConsoleNamespace disconnect")


	async def on_my_event(self, sid, data):
		logger.debug("SLConsoleNamespace on_my_event %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.namespace)
		return "ACK"

	async def on_need_log(self, sid, data):
		logger.debug("SLConsoleNamespace need_log %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.namespace)
		node = data.get('id',None)
		if  not node in Node.name_idx:
			return "NAK"
		try:
			r = Node.name_idx[node].console_pkt_log(data['key'], int(data['count']))
		except:
			return "NAK"
		return "ACK"


	async def on_join(self, sid, message):
		logger.debug("SLConsoleNamespace on_join %s", message)
		if not 'room' in message:
			logger.error("sio join: message without room: %s", str(message))
			return "NAK"
		room = Room(sroom=message['room'])
		sroom = str(room)
		self.enter_room(sid, sroom, namespace=self.namespace)
		if room.is_item_room():		# item_key_*
			item = registry.find_by_id(room.lvl, int(room.key))
			if item is None:
				logger.debug("SLConsoleNamespace no items in room  %s", room)
				return "NAK"
			items_to_send = []
			for i in item.children:
				items_to_send.append(item.children[i])
		elif not room.is_header():		# item_*
			items_to_send = registry.get_all(room.lvl)
		else:						# item_key
			item = registry.find_by_id(room.lvl, int(room.key))
			if item is None:
				logger.debug("SLConsoleNamespace no items in room  %s", room)
				return "NAK"
			items_to_send = [item]

		logger.debug("SLConsoleNamespace items_to_send %s", items_to_send)
		for item in items_to_send:
			item.console_update([room])
#			data_id, data_to_send = item.gen_console_data()
#			pack =  {
#			  'id': data_id,
#			  'type': room.lvl, 
#			  'display_vals':  data_to_send,
#			}
#			if room.is_header():
#				pack['header'] = True
#			await self.webapp.a_send_con_upd(sroom, pack)
		return "ACK"

	async def on_leave(self, sid, message):
		logger.debug("SLConsoleNamespace on_leave %s", message)
		self.leave_room(sid, message['room'], namespace=self.namespace)
		return "ACK"


#
# WebApp
#
class WebApp(object):

	def __init__(self, namespace, sio, conf, loop = None):
		self.name = "WebApp"
		self.conf = conf
		self.minupdinterval = conf['minupdinterval']
		self.sio = sio
		self.namespace = namespace
		self.loop = loop
		self.con_upd_q = asyncio.Queue(loop=self.loop)
		self.app = web.Application()
		self.app._set_loop(self.loop)
		self.sio.attach(self.app)
		self.app['websockets'] = []
		self.app.router.add_static('/',LIB_DIR+"/html")
		self.app.router.add_get('/config.json', self.config_json)
		self.app.on_cleanup.append(self.web_on_cleanup)
		self.app.on_shutdown.append(self.web_on_shutdown)
		self.backlog = 128

		self.shutdown_timeout = self.conf['shutdown_timeout']
#		self.api_password = conf['api_password']
		self.ssl_certificate = conf['ssl_certificate']
		self.ssl_key = conf['ssl_key']
		self.ssl_context = None
		self.access_log_format = None
		self.access_log = access_logger

		self.host = conf['host']
		self.port = conf['port']
	#		self._handler = None
	#		self.server = None



	async def qstart(self):
		logger.info("%s starting q handler", self.name)
		self.conf_upd_res = await self.con_upd_t()
	

	async def start(self):
		logger.info("%s starting, server %s port %s", self.name, self.host,  self.port)
		self.sio.register_namespace(SLConsoleNamespace(self))

		await self.app.startup()
	
		scheme = 'https' if self.ssl_context else 'http'
		base_url = URL.build(scheme=scheme, host='localhost', port=self.port)
		uri = str(base_url.with_host(self.host).with_port(self.port))
	
		make_handler_kwargs = dict()
		if self.access_log_format is not None:
			make_handler_kwargs['access_log_format'] = self.access_log_format

		self.handler = self.app.make_handler(loop=self.loop,
						access_log=self.access_log,
						**make_handler_kwargs)

		self.server = await self.loop.create_server(
						self.handler, self.host, self.port, 
						ssl=self.ssl_context,
						backlog=self.backlog)


	def stop(self):
		self.server.close()
		self.loop.run_until_complete(self.server.wait_closed())
		self.loop.run_until_complete(self.app.shutdown())
		self.loop.run_until_complete(self.handler.shutdown(self.shutdown_timeout))
		self.loop.run_until_complete(self.app.cleanup())
	

	async def index(self, request):
		index_html = self['index']
		with open(index_html) as f:
			return web.Response(text=f.read(), content_type='text/html')


	async def config_json(self, request):
		rj = json.dumps(self.conf)
		return web.Response(text=rj, content_type='application/json')


	def web_on_cleanup(self, app):
		logger.info("web closing down")


	async def web_on_shutdown(self, app):
		for ws in self.app['websockets']:
			await ws.close(code=WSCloseCode.GOING_AWAY, message='Server shutdown')


	async def a_send_con_upd(self, item, rooms):
		logger.debug("a_send_con_upd: q size: %s", self.con_upd_q.qsize())
		await self.con_upd_q.put([item, rooms])


	async def con_upd_t(self):
		while True:	
			item, rooms =  await self.con_upd_q.get() 
			logger.debug("con_upd_t get entry %s for room %s", item.name, rooms)
			if rooms is None:
				continue
			nrooms = []
			for room in rooms:
				sroom = str(room)
				if item.last_updates.get(sroom,0) <= \
						 (self.loop.time() - self.minupdinterval):
					nrooms.append(room)
					item.last_updates[sroom] = self.loop.time()
					if sroom in item.future_updates:
						del item.future_updates[sroom]
				elif sroom not in item.future_updates:
					item.future_updates[sroom] = item.last_updates[sroom] + self.minupdinterval

			res = item.console_update(nrooms)
			for sroom, data in res:
				logger.debug("con_upd_t ROOM %s EMIT %s" % (sroom, data))
				await self.sio.emit('data_full', data, 
						namespace=self.namespace, room=sroom)
		self.con_upd_q.task_done()
