import asyncio
import socketio
import signal

from aiohttp import web
from aiohttp.log import access_logger, web_logger


from .linkage import (
	registry,
	BaseItem,
	Room,
)

from .const import (
	LIB_DIR,
	INDEX_HTML,
)

import logging
logger = logging.getLogger(__name__)

from yarl import URL


#
# WebNameSpace
#
class WebNamespace(socketio.AsyncNamespace):
	def __init__(self, webapp):
		self.webapp = webapp
		self.namespace = self.webapp.namespace

		super().__init__(self.namespace)
		logger.debug("WebNamespace registered for namespace %s", self.namespace)


	def on_connect(self, sid, environ):
		logger.debug("WebNamespace connect %s",str(environ['REMOTE_ADDR']))


	def on_disconnect(self, sid):
		logger.debug("WebNamespace disconnect")
		for r in registry.get_all('Room'):
			if sid in r.members:
				logger.debug("WebNamespace %s removed from room %s", sid, r)
				del r.members[sid]


	async def on_my_event(self, sid, data):
		logger.debug("WebNamespace on_my_event %s", data)
	#	await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.namespace)
		return "ACK"


	async def on_need_log(self, sid, data):
		logger.debug("WebNamespace need_log %s", data)
	#	await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.namespace)
		node = data.get('id',None)
		if  not node in Node.name_idx:
			return "NAK"
		try:
			r = Node.name_idx[node].console_pkt_log(data['key'], int(data['count']))
		except:
			return "NAK"
		return "ACK"


	async def on_join(self, sid, message):
		logger.debug("WebNamespace on_join %s", message)
		if not 'room' in message:
			logger.error("join: message without room: %s", str(message))
			return "NAK"
		sroom=message['room']
		room = registry.find_by_id('Room', sroom)
		if room is None:
			room = Room(sroom=sroom) 	
			logger.debug("join: auto create room: %s", str(message))

		self.enter_room(sid, sroom, namespace=self.namespace)
		room.add_member(sid)
		logger.debug("WebNamespace items_to_send %s", room.name)
		room.schedule_update(sid)	# update all items in the room for this sid only
		return "ACK"


	async def on_leave(self, sid, message):
		logger.debug("WebNamespace on_leave %s", message)
		if not 'room' in message:
			logger.error("leave: message without room: %s", str(message))
			return "NAK"
		sroom=message['room']
		room = registry.find_by_id('Room', sroom)
		if room is None:
			return "NAK"
		if not room.is_private(sid):
			self.leave_room(sid, message['room'], namespace=self.namespace)
		room.del_member(sid)
		return "ACK"


	async def on_move(self, sid, message):
		logger.debug("WebNamespace on_move %s", message)
		if not 'room' in message:
			logger.error("on_move: message without room: %s", str(message))
			return "NAK"
		sroom=message['room']
		key = message.get('key', '')
		count = message.get('count', '')
		if key == '':
			key = None
		if count != '':
			try:
				count = int(count)
			except:
				count = 0

		room = registry.find_by_id('Room', sroom)
		if room is None:
			logger.error("on_move: no room: %s", str(message))
			return "NAK"
		if not sid in room.members:
			logger.error("on_move: no sid  %s in room %s", sid, str(message))
			return "NAK"
		if key is None:
			logger.error("on_move: no key for sid %s in room %s", sid, str(message))
			return "NAK"
		if not room.is_private(sid):		# make this a private room member
			room.add_member(sid, key, count)
			self.leave_room(sid, message['room'], namespace=self.namespace)
		room.members[sid].set_position(key, count)
		# return items in range?
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
	#	self.api_password = conf['api_password']
		self.ssl_certificate = conf['ssl_certificate']
		self.ssl_key = conf['ssl_key']
		self.ssl_context = None
		self.access_log_format = None
		self.access_log = access_logger

		self.host = conf['host']
		self.port = conf['port']
	#	self._handler = None
	#	self.server = None



	async def qstart(self):
		logger.info("%s starting q handler", self.name)
		self.con_upd_res = await self.console_update_loop()


	async def start(self):
		logger.info("%s starting, server %s port %s", self.name, self.host,  self.port)
		self.sio.register_namespace(WebNamespace(self))

		await self.app.startup()
		logger.debug("%s: app started", self.name)

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


	def schedule_update(self, roomitem, sroom, force):
		logger.debug("webapp schedule_update for %s item %s", roomitem.room, roomitem.item)
		asyncio.ensure_future(self.con_upd_q.put([roomitem, sroom, force]), loop=self.loop)


	async def console_update_loop(self):
		logger.info("%s q handler", self.name)
		while True:
			upd_roomitem, upd_sroom, upd_force =  await self.con_upd_q.get()
			if upd_roomitem is None:
				break

#			if len(upd_roomitem.room.members) == 0:		# nobody in the room
#				logger.debug("console_update_loop empty room %s", upd_roomitem.room)
#				continue
		
			await self.sio.emit('data_full', 
						data = upd_roomitem.console_update(upd_force),
						namespace = self.namespace,
						room = upd_sroom)	
			upd_roomitem.update_sent()
			self.con_upd_q.task_done()

		logger.debug("console_update_loop done")


