#!/usr/bin/python
"""Small Python (2.5) app to send heartbeats from planetlab nodes."""

import simplejson
import os
import random
import socket
import sys
import time


class Heartbeat:
    def __init__(self, server, port, nodename, secret):
        self.server = server
        self.port = port
        self.nodename = nodename
        self.secret = secret

    def __get_cpu(self):
        try:
            result = os.popen("top -n 1 -b | head -1 | awk '{print $NF}'")
            return float(result.read().strip())
        except:
            return "N/A"

    def __get_memory(self):
        try:
            result = os.popen("free -t | tail -1 | awk '{print $2, $3}'")
            total, used = result.read().strip().split()
            return float(used) / float(total)
        except:
            return "N/A"

    def log(self, message):
        os.system("echo '%s' > /tmp/instrument.log" % message)

    def send(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            message = simplejson.dumps({
                "node": self.nodename,
                "secret": self.secret,
                "cpu": self.__get_cpu(),
                "memory": self.__get_memory(),
            })
            sock.sendto(message, (self.server, self.port))
            self.log(message)
        except Exception, e:
            self.log(str(e))


if __name__ == "__main__":
    args = sys.argv
    if len(args) != 4:
        print("Usage: ./instrument.py hostname:port nodename secret")
        sys.exit(1)

    # (mis)use Linux abstract sockets as a mutex
    lock_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    lock_socket.bind('\0' + "instrument.py")

    hostname, port = args[1].split(":")
    heartbeat = Heartbeat(hostname, int(port), args[2], args[3])

    while True:
        heartbeat.send()

        # randomize the heartbeat to avoid overloading server
        delay = 600 + 300 * random.random()
        time.sleep(delay)
