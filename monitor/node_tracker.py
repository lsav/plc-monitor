"""
Stateful class that tracks node health data.
"""
from collections import deque
from datetime import datetime, timedelta
from math import inf
import os
import threading
import time

from .update import get_node_data


class NodeTracker:
    HEADINGS = ["Host", "Is Alive", "Latency (ms)", "SCP Time (ms)", 
            "Uptime (%)", "CPU (%)", "Memory (%)"]

    def __init__(self, hosts_file):
        self.hosts_file = hosts_file
        hosts = self.__load_nodes()
        self.last_update = datetime.now()

        # host history table, track # times host was up in the last 24 hours
        # key = hostname, val = queue of (time, boolean)
        self.history = {x: deque() for x in hosts}

        # key = hostname, val = host's health info
        self.data = {x: self.__host_datum_dict() for x in hosts}

        self.lock = threading.Lock()

    def __load_nodes(self, path=None):
        with open(os.path.join("resources", path or self.hosts_file)) as f:
            return [x.strip() for x in f.readlines() if not x.isspace()]

    def __host_datum_dict(self):
        return {
            "uptime": 0, 
            "latency": inf, 
            "is_alive": False, 
            "scp_time": inf,
            "cpu": "0",
            "memory": "0",
        }

    def __sync_hosts(self, hosts):
        """Update host tables to be the intersection of earlier known hosts
        and the new hosts list.
        """
        diff = set(hosts) - set(self.history.keys())
        if len(diff):
            self.history.update({x: deque()} for x in hosts)
            self.data.update({x: self.__host_datum_dict() for x in hosts})

    def update(self):
        """Update host information."""
        hosts = self.__load_nodes()
        node_data = get_node_data(hosts)

        now = datetime.now()
        self.__sync_hosts(node_data.keys())

        self.lock.acquire()
        self.last_update = now

        for host, history in self.history.items():
            is_alive = node_data.get(host, [inf, inf])[0] < inf
            history.append((now, is_alive))
            self.data[host][is_alive] = is_alive

            # trim off entries that are too old
            while history[0][0] + timedelta(days=1) < now:
                history.popleft()

            # update is_alive, uptime
            self.data[host]['is_alive'] = is_alive
            if len(history):
                times_alive = sum([x[1] for x in history])
                self.data[host]['uptime'] = times_alive / len(history)
            else:
                self.data[host]['uptime'] = 0

        # update rest of data
        for host, datum in self.data.items():
            datum['latency'] = node_data.get(host, [inf, inf])[0]
            datum['scp_time'] = node_data.get(host, [inf, inf])[1]

        # ignore cpu & memory for now

        self.lock.release()

    def __schedule_updates(self, delay):
        while True:
            self.update()
            time.sleep(delay * 60)
        
    def start(self, delay=10):
        """Schedule periodic updates."""
        self.update_thread = threading.Thread(target=self.__schedule_updates, 
                                              args=(delay,))
        self.update_thread.start()

    def get_output(self):
        """Return a tuple (time, data), where `time` is the last time the
        node data was updated, and `data` is a 2D array."""
        output = []

        self.lock.acquire()
        for host, datum in self.data.items():
            # only return nodes that have been alive in the last 24 hours
            if datum['uptime'] == 0:
                continue

            is_alive = "yes" if datum['is_alive'] else "no"
            latency = "{:.2f}".format(datum['latency'] * 1000)
            scp_time = "{:.2f}".format(datum['scp_time'] * 1000)
            uptime = "{:.0f}".format(datum['uptime'] * 100)
                
            output.append([host, is_alive, latency, scp_time, uptime, 
                datum['cpu'], datum['memory']])

        last_update = self.last_update.strftime("%-I:%M %p, %b %-d, %Y")
        self.lock.release()
        return last_update, output


if __name__ == '__main__':
    # test the module
    tracker = NodeTracker('testnodes.txt')
    tracker.start()
    while True:
        print(*tracker.get_output())
        time.sleep(100)
