#!/usr/bin/python
"""
Small Python (2.5) app to send heartbeats from planetlab nodes.
"""
import simplejson
import os
import random
import socket
import sys
import time


class Heartbeat:
    def __init__(self, server, port, nodename):
        self.server = server
        self.port = port
        self.nodename = nodename

    def __get_cpu(self):
        """Average CPU utilization in the last 15 min, according to top."""
        try:
            result = os.popen("top -n 1 -b | head -1 | awk '{print $NF}'")
            return float(result.read().strip())
        except Exception, e:
            return "N/A"

    def __get_memory(self):
        """Average current memory use."""
        try:
            result = os.popen("free -t | tail -1 | awk '{print $2, $3}'")
            total, used = result.read().strip().split()
            return float(used) / float(total)
        except Exception, e:
            return "N/A"

    def send(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            message = {
                "node": self.nodename,
                "cpu": self.__get_cpu(),
                "memory": self.__get_memory(),
            }
            sock.sendto(json.dumps(message), (self.server, self.port))
        except Exception, e:
            pass


if __name__ == "__main__":
    args = sys.argv
    if len(args) != 3:
        print("Usage: ./instrument.py hostname:port nodename")
        sys.exit(1)

    # (mis)use Linux abstract sockets as a mutex
    lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        lock_socket.bind('\0' + "instrument.py")
    except socket.error:
        # another instance of this script is running
        sys.exit(0)

    hostname, port = args[1].split(":")
    nodename = args[2]
    heartbeat = Heartbeat(hostname, int(port), nodename)

    random.seed(nodename)

    while True:
        heartbeat.send()

        # randomize the heartbeat -- help avoid overloading server
        delay = 600 + 300 * random.random()
        time.sleep(delay)  # send a beat every 10-15 min
