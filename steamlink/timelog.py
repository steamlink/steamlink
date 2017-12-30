import time
import collections

#
# TimeLog
#
class TimeLog:
	def __init__(self, maxitems):
		self.maxitems = maxitems
		self.items = collections.OrderedDict()

	def add(self, item):
		while len(self.items) >= self.maxitems:
			self.items.popitem(last=False)
		self.items[time.time()] = item


	def get(self, where, count):
		keys = list(self.items.keys())
		if where in [None, '', 'last']:
			pos = len(keys)
		else:
			try:
				pos = keys.index(where)
			except:
				pos = 0		# return oldest entry if key not found
				count = abs(count)
		if count < 0:
			start = max(0, (pos + count))
			end = max(0, pos)
		else:
			start = min(pos+1, len(keys))
			end = min(pos+1+count, len(keys))
		logger.debug("TimeLog.get: pos %s start %s end %s len %s", pos, start, end, len(keys))
		r = {}
		for i in range(start, end):
			r[keys[i]] =  str(self.items[keys[i]])
		return r

if __name__ == '__main__':
	l = TimeLog(10)

	for i in range(20):
		l.add("I-%s" % i)

	r = l.get('', -2)
	print(r)
	r = l.get(list(r.keys())[0], -2)
	print(r)
	r = l.get(list(r.keys())[-1], 2)
	print(r)
	r = l.get(list(r.keys())[-1], 2)
	print(r)



