
import asyncio
import socketio
from aiohttp import web

from steamlink.steamlink import (
	Room,
	Steam,
	Mesh,
	Node,
	SL_OP,
	Packet,
	LogData,
	Mqtt,
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

class SLConsoleNamespace(socketio.AsyncNamespace):
	def __init__(self, steam, sio):
		self.steam = steam
		self.sio = sio
		super().__init__(self.steam.ns)
		logger.debug("SLConsoleNamespace registered for ns %s", self.steam.ns)


	def on_connect(self, sid, environ):
		logger.debug("SLConsoleNamespace connect %s",str(environ['REMOTE_ADDR']))

	def on_disconnect(self, sid):
		logger.debug("SLConsoleNamespace disconnect")


	async def on_my_event(self, sid, data):
		logger.debug("SLConsoleNamespace on_my_event %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.steam.ns)
		return "ACK"

	async def on_need_log(self, sid, data):
		logger.debug("SLConsoleNamespace need_log %s", data)
#		await self.emit('my_response', {'data': data['data']} ) #, room=sid, namespace=self.steam.ns)
		node = data.get('id',None)
		if  not node in Node.name_idx:
			return "NAK"
		try:
			r = Node.name_idx[node].console_pkt_log(data['key'], int(data['count']))
		except:
			return "NAK"
		return "ACK"


	async def emit_data_full(self, sroom, data):
		logger.debug("ROOM %s EMIT %s" % (sroom, data))
		await self.sio.emit('data_full', data, namespace=self.steam.ns, room=sroom)


	async def on_join(self, sid, message):
		logger.debug("SLConsoleNamespace on_join %s", message)
		if not 'room' in message:
			logger.error("sio join: message without room: %s", str(message))
			return "NAK"
		room = Room(sroom=message['room'])
		sroom = str(room)
		self.enter_room(sid, sroom, namespace=self.steam.ns)
		if room.is_item_room():		# item_key_*
			item = registry.find_by_id(room.lvl, int(room.key))
			if item is None:
				logger.debug("SLConsoleNamespace no items in room  %s", room)
				return "NAK"
			items_to_emit = []
			for i in item.children:
				items_to_emit.append(item.children[i])
		elif not room.is_header():		# item_*
			items_to_emit = registry.get_all(room.lvl)
		else:						# item_key
			item = registry.find_by_id(room.lvl, int(room.key))
			if item is None:
				logger.debug("SLConsoleNamespace no items in room  %s", room)
				return "NAK"
			items_to_emit = [item]

		logger.debug("SLConsoleNamespace items_to_emit %s", items_to_emit)
		for item in items_to_emit:
			data_id, data_to_emit = item.gen_console_data()
			pack =  {
			  'id': data_id,
			  'type': room.lvl, 
			  'display_vals':  data_to_emit,
			}
			await self.emit_data_full(sroom, pack)
		return "ACK"

	async def on_leave(self, sid, message):
		logger.debug("SLConsoleNamespace on_leave %s", message)
		self.leave_room(sid, message['room'], namespace=self.steam.ns)
		return "ACK"



#
# WebApp
#
class WebApp(object):

	def __init__(self, steam, sio, conf, loop = None):
		self.name = "WebApp"
		self.conf = conf
		self.sio = sio
		self.steam = steam
		if loop is None:
			self.loop = asyncio.get_event_loop()
		else:
			self.loop = loop
		self.app = web.Application()
		self.sio.attach(self.app)
		self.app['websockets'] = []
		self.app.router.add_static('/',LIB_DIR+"/html")
		self.app.router.add_get('/config.json', self.config_json)
		self.app.on_cleanup.append(self.web_on_cleanup)
		self.app.on_shutdown.append(self.web_on_shutdown)

#		self.api_password = conf.get('api_password', None)
#		self.ssl_certificate = conf.get('ssl_certificate', None)
#		self.ssl_key = conf.get('ssl_key', None)
		self.host = conf.get('host', '127.0.0.1')
		self.port = conf.get('port', 8080)
#		self._handler = None
#		self.server = None


	def start(self):
		logger.info("%s starting, server %s port %s", self.name, self.host,  self.port)
		self.sio.register_namespace(SLConsoleNamespace(self.steam, self.sio))
		shutdown_timeout = int(self.conf.get('shutdown_timeout','60'))

#		self.loop.run_until_complete(app.startup())
		web.run_app(self.app,
			host=self.host,
			port=self.port,
			shutdown_timeout=shutdown_timeout,
			loop=self.loop)
	

	async def index(self, request):
		index_html = self.conf.get('index',INDEX_HTML)
		with open(index_html) as f:
			return web.Response(text=f.read(), content_type='text/html')


	async def config_json(self, request):
		rj = json.dumps(self.conf)
		return web.Response(text=rj, content_type='application/json')


	def web_on_cleanup(self, app):
		logger.info("web closing down")


	async def web_on_shutdown(self, app):
		for ws in self.app['websockets']:
			await ws.close(code=WSCloseCode.GOING_AWAY,
						   message='Server shutdown')


