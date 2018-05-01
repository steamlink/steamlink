import asyncio
import socketio
import os
import json
import aiohttp_jinja2
import jinja2
import yaml

from aiohttp import web
from aiohttp.log import access_logger, web_logger


from .steamlink import (
	Mesh,
	Node,
	Steam,
	Packet
)
from .linkage import (
	registry,
	BaseItem,
	Room,
)

import logging
logger = logging.getLogger(__name__)

from yarl import URL

class DisplayConfiguration:   

	def __init__(self, file_name):
		self.data = {}
		f = open(file_name)
		self.data = yaml.load(f)
		f.close()

	def row_wise(self):
		rows = {}
		for key in self.data:
			item = self.data[key]
			try:
				if item['row'] not in rows:
					logger.debug("web rowise: itemrow %s ", item['row'])
					rows[item['row']] = []
				rows[item['row']].append(item)
			except KeyError:
				logger.error("DisplayConfiguration: No key found")
		return rows

class NavBar:

	def __init__(self, dir):
		self.yamls = []
		for file in os.listdir(dir):
			name, ext = file.rsplit('.', 1)
			if ext == 'yaml':
				self.yamls.append(name)


XXX = """
#
# ItemPartialMap
#
class ItemPartialMap:
#	 track all partials per item 
	def __init__(self, rectype):
		self.itype = 'ItemPartialMap'
		self.key = rectype
		self.name = rectype		# remo
		self.map = {}		# key is item key, value is Instance of Partial
		if logging.DBG > 2: logger.debug("ItemPartialMap: created %s", name)
		registry.register(self)


	def add(self, itemkey, partial):
		if not itemkey in self.map:
			self.map[itemkey] = []
		self.map[itemkey] += [parial]


	def del(self, partial, itemkey):
		if not itemkey in self.map:
			logger.error("ItemPartialMap del: item %s not in map", itemkey)
			return
		try:
			idx = self.map.find(partial)
		except:
			logger.error("ItemPartialMap del: partial %s not in mapentry for %s", partial, itemkey)
			return
		del self.map[idx]


irectypes = ['Steam', 'Mesh', 'Node', 'Packet']

#
# Partial
#
class Partial:
	def __init__(self, stream_tag, rectype, key_field, start_key, count, return_children):
		self.itype = 'Partial'
		self.key = stream_tag
		self.name = stream_tag
		registry.register(self)

		self.return_children = return_children 		

		self.item = registry.find_by_id(rectype, start_key)	
		if self.return_children:		# find the correct rectype
			self.items_source = self.item.children
			idx = irectypes.index(rectype)
			rectype = irectypes[idx + 1]
			if count > 0:
				start_key = self.items_source[0].key
			else:
				start_key = self.items_source[-1].key
		else:
			count = 1
			self.items_source = [self.item]


		self.rectype = rectype
		if key_field != None:
			self.key_field = key_field
		else:
			self.key_field = self.item.getkeyfield()

		self.setstream(start_key, count, end_key, force)

		self.ItemPatialMap = registry.find_by_name('ItemPartialMap', self.rectype)
		if self.ItemPatialMap == None:
			self.ItemPatialMap = ItemPartialMap(self.rectype)

		self.items = {}


	def __del__(self):	
		for item in self.items[]:
			self.ItemPatialMap.del(self, item)


	def setstream(self, start_key, count, end_key, force):
		self.start_key = start_key
		self.end_key = end_key
		self.count = count
		if 


	def additem(self, item):
		self.items[item.key] = item
		self.ItemPatialMap.add(item.key, self)


	def delitem(self, item):
		del self.items[item.key] 
		self.ItemPatialMap.del(item.key, self)


#
# Web Session
#
class WebSession:
	def __init__(self, sid):
		self.sid = sid
		self.partials = {}
		


	def add_partial(self, stream_tag, partial ):
		self.partials[stream_tag] = partial



class 
"""


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


	def mk_roomid(self, message):
		# message: { record_type:.., key_field:.., start_key:.., stream_tag:..,
		#				count: .., end_key:..., return_children:..,  force: .. }
		if message['count'] == 0:
			sroom = "%s_%s" % (message['record_type'], message['start_key'])
		else:
			sroom = "%s_%s_*" % (message['record_type'], message['start_key'])
		return (sroom, message['stream_tag'])


	async def on_startstream(self, sid, message):
		logger.debug("WebNamespace on_startstream %s", message)
#		if not 'room' in message:
#			logger.error("join: message without room: %s", str(message))
#			return "NAK"

		sroom, stream_tag = self.mk_roomid(message)
#		sroom=message['room']
		room = registry.find_by_id('Room', sroom)
		if room is None:
			room = Room(sroom=sroom) 	
			logger.debug("join: auto create room: %s %s", sroom, str(message))

		room.stream_tag = stream_tag
		self.enter_room(sid, sroom, namespace=self.namespace)
		room.add_member(sid)
		logger.debug("WebNamespace items_to_send %s", room.name)
		room.schedule_update(sid)	# update all items in the room for this sid only
		if message['count'] != 0:
			try:
				itype = eval("%s.childclass" % room.ritype)
			except Exception as e:
				logger.error("unknown room type %s", e)
				return
			if itype == '':
				itype = "Packet"
		else:
			itype = room.ritype
		skf = "%s.keyfield" % itype
		kf = eval(skf)
		logger.debug("WebNamespace itype %s keyfield %s", room.ritype, kf)
		res = { 'key_field': kf,
				'record_type': itype
			  }
		return res


	async def on_leave(self, sid, message):
		logger.debug("WebNamespace on_leave %s", message)
		if not 'room' in message:
			logger.error("leave: message without room: %s", str(message))
			return "NAK"
#		sroom=message['room']
		sroom, stream_tag = self.mk_roomid(message)
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
#		sroom=message['room']
		sroom, stream_tag = self.mk_roomid(message)
		# default anchor is ID, sort key later?
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
		self.app.router.add_get('/config.json', self.config_json)

		self.libdir = os.path.dirname(os.path.abspath(__file__))
		self.static_dir = self.libdir+'/html/static'
		self.templates_dir = self.libdir+'/html/templates'
		self.app.router.add_route('GET', '/', self.route_handler)
		self.app.router.add_route('GET', '/favicon.ico', self.favicon_handler)
		self.app.router.add_route('GET', '/{file_name}', self.route_handler)

		
		self.app.router.add_static('/static', self.static_dir)
		self.app.on_cleanup.append(self.web_on_cleanup)
		self.app.on_shutdown.append(self.web_on_shutdown)
		self.backlog = 128
		
		aiohttp_jinja2.setup(self.app, loader=jinja2.FileSystemLoader(self.templates_dir))

		self.shutdown_timeout = self.conf['shutdown_timeout']
	#	self.api_password = conf['api_password']
		self.ssl_certificate = conf['ssl_certificate']
		self.ssl_key = conf['ssl_key']
		self.ssl_context = None
		self.access_log_format = 'XXX %a %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"'
		self.access_log = access_logger
		access_logger.setLevel(logging.WARN)	# N.B. set in config

		self.host = conf['host']
		self.port = conf['port']
	#	self._handler = None
	#	self.server = None

	def __getstate__(self):
		return {'MyClass': 'WebApp'}

	async def qstart(self):
		logger.info("%s starting q handler", self.name)
		self.con_upd_res = await self.console_update_loop()


	async def start(self):
		logger.info("%s starting, server %s port %s", self.name, self.host,  self.port)
		self.sio.register_namespace(WebNamespace(self))
		self.runner = web.AppRunner(self.app)
		await self.runner.setup()
		self.site = web.TCPSite(self.runner, 'localhost', self.port)
		await self.site.start()

		logger.debug("%s: app started", self.name)

		scheme = 'https' if self.ssl_context else 'http'

		make_handler_kwargs = dict()
		make_handler_kwargs['access_log_format'] = self.access_log_format
		make_handler_kwargs['access_log'] = self.access_log

		self.handler = self.app.make_handler(loop=self.loop, **make_handler_kwargs)

		self.server = await self.loop.create_server(
						self.handler, self.host, self.port,
						ssl=self.ssl_context,
						backlog=self.backlog)


	def stop(self):
		self.server.close()
		self.loop.run_until_complete(self.server.wait_closed())
		self.loop.run_until_complete(self.app.shutdown())
		self.loop.run_until_complete(self.handler.shutdown(self.shutdown_timeout))
#		self.loop.run_until_complete(self.runner.cleanup())


	async def config_json(self, request):
		rj = json.dumps(self.conf)
		return web.Response(text=rj, content_type='application/json')


	def web_on_cleanup(self, app):
		logger.info("web closing down")


	async def web_on_shutdown(self, app):
		for ws in self.app['websockets']:
			await ws.close(code=WSCloseCode.GOING_AWAY, message='Server shutdown')


	def queue_item_update(self, roomitem, sroom, force):
		if logging.DBG >= 2: logger.debug("webapp queue_item_update for %s item %s", roomitem.room, roomitem.item)
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
		
#			await self.sio.emit('data_full', 
			data = upd_roomitem.console_update(upd_force)
			if upd_roomitem.room.stream_tag == None:
				if logging.DBG >= 2: logger.debug("console_update_loop: stream_tag is None: %s", data)
			else:
				await self.sio.emit(upd_roomitem.room.stream_tag, 
						data = data,
						namespace = self.namespace,
						room = upd_sroom)	
				if logging.DBG > 1: logger.debug("console_update_loop: data sent to stream_tag %s: %s", \
						upd_roomitem.room.stream_tag, data)
			upd_roomitem.update_sent()
			self.con_upd_q.task_done()

		logger.debug("console_update_loop done")


	async def favicon_handler(self, request):
		return None

	async def route_handler(self, request):
		nav = NavBar(self.templates_dir)

		if request.rel_url.path == '/':
			file_name = 'index'
		else:
			file_name = request.match_info['file_name']
			# file_name = str(request.rel_url.path).rstrip('/')
		logger.info("web route_handler: filename: %s, request.rel_url %s", file_name, request.rel_url)
		dc = DisplayConfiguration(self.templates_dir + '/' + file_name + '.yaml')

		for qk in request.query:
			logger.debug("web route_handler: Query' : %s =%s", qk, request.query[qk])
			try:
				partial, key = qk.split('.', 1)
			except:
				logger.debug("web route_handler: Query key with no '.' : %s", qk)
				continue
			if not partial in dc.data:
				logger.debug("web route_handler: partial not in yaml : %s", partial)
				continue
			logger.debug("web route_handler: dc.data[partial] : %s", dc.data[partial])
			i = request.query[qk]
			try:
				i = int(i)
			except:
				pass
			dc.data[partial][key] = i

		if logging.DBG > 0: logger.debug("webapp handler %s", dc.data)
		context = { 'context' : dc.row_wise(), 'navbar' : nav.yamls }
		
		if not (os.path.isfile(self.templates_dir + '/'+ file_name + '.html')):
			file_name = 'index'
		response = aiohttp_jinja2.render_template(file_name + '.html', request, context)
#		response.headers['Content-Language'] = 'en'
		return response
