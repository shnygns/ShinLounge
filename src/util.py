import itertools
import time
import logging
import os
import inspect 
from datetime import datetime
from queue import PriorityQueue
from threading import Lock
from datetime import timedelta

class Scheduler():
	def __init__(self):
		self.tasks = [] # list of [interval, next_trigger, func]
	@staticmethod
	def _wrapped_call(f, data, ev):
		try:
			# Get the function's parameter names
			params = inspect.signature(f).parameters
			# Check if the function requires 'data' and 'ev' parameters
			if 'data' in params and 'ev' in params:
				f(data, ev)
			elif 'data' in params:
				f(data)
			elif 'ev' in params:
				f(ev)
			else:
				f()
		except Exception as e:
			logging.exception("Exception raised during scheduled task")
	def register(self, func, name="", data=[], ev=None, first_run=1, **kwargs):
		if kwargs:
			interval = timedelta(**kwargs) // timedelta(seconds=1)
			assert interval > 0
		else:
			interval = 0  # Default for single-run tasks
		
		# Calculate next_trigger based on the first_run delay
		next_trigger = int(time.monotonic()) + first_run
		self.tasks.append([name, func, data, interval, next_trigger, ev])

	def get_job_by_name(self, name):
		for task in self.tasks:
			if task[0] == name:
				return task
		return None
	
	def run(self):
		while True:
			now = int(time.monotonic())
			# Run tasks that have expired
			for e in self.tasks[:]:  # Iterate over a copy to allow removal
				if now >= e[4]:  # next_trigger time has been reached
					Scheduler._wrapped_call(e[1], e[2], e[5])
					if e[3] == 0:  # interval is 0, run only once
						self.tasks.remove(e)
					else:
						e[4] = now + e[3]  # Reschedule for the next interval
			
			# Wait until a task expires
			if self.tasks:
				now = int(time.monotonic())
				wait = min((e[4] - now) for e in self.tasks)
				if wait > 0:
					time.sleep(wait)

class MutablePriorityQueue():
	def __init__(self):
		self.queue = PriorityQueue() # contains (prio, iid)
		self.items = {} # maps iid -> opaque
		self.counter = itertools.count()
		# protects `items` and `counter`, `queue` has its own lock
		self.lock = Lock()
	def get(self):
		while True:
			_, iid = self.queue.get()
			with self.lock:
				# skip deleted entries
				if iid in self.items.keys():
					return self.items.pop(iid)
	def put(self, prio, data):
		with self.lock:
			iid = next(self.counter)
			self.items[iid] = data
		self.queue.put((prio, iid))
	def delete(self, selector):
		with self.lock:
			keys = list(self.items.keys())
			for iid in keys:
				if selector(self.items[iid]):
					del self.items[iid]

class Enum():
	def __init__(self, m, reverse=True):
		assert len(set(m.values())) == len(m)
		self._m = m
		if reverse:
			self.reverse = Enum({v: k for k, v in m.items()}, reverse=False)
	def __getitem__(self, key):
		return self._m[key]
	def __getattr__(self, key):
		return self[key]
	def keys(self):
		return self._m.keys()
	def values(self):
		return self._m.values()

def _salt(c):
	c = ord(c)
	if 58 <= c <= 64: # ':' - '@' maps to 'A' - 'G'
		return chr(c + 7)
	elif 91 <= c <= 96: # '[' - '`' maps to 'a' - 'f'
		return chr(c + 6)
	elif 46 <= c <= 122: # '.' - 'Z' stays
		return chr(c)
	return '.'

def genTripcode(tripcode):
	# doesn't actually match 4chan's algorithm exactly
	pos = tripcode.find("#")
	trname = tripcode[:pos]
	trpass = tripcode[pos+1:]

	salt = (trpass[:8] + 'H.')[1:3]
	salt = "".join(_salt(c) for c in salt)

	trip_final = __import__("crypt").crypt(trpass[:8], salt)

	return trname, "!" + trip_final[-10:]

def getLastModFile(dir="", exts=("", ".py", ".txt", ".md", ".example")):
	path = os.path.abspath(dir)
	files = [{
		"name": file.name,
		"last_mod": datetime.fromtimestamp(file.stat().st_mtime),
		"path": file.path,
		"dir": dir
	} for file in os.scandir(path) if file.is_file() and os.path.splitext(file)[1] in exts]
	return max(files, key=lambda file: file["last_mod"])

def get_users_active_elsewhere(shared_db, config):
	if shared_db is None:
		return
	hub_dict = shared_db.get_active_users()
	ae = {u['user_id'] for u in hub_dict if (u['current_active_lounge'] != config['bot_token'] and len(u['current_active_lounge']) > 2)}
	return ae