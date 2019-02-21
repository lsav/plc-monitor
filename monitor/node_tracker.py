"""
Stateful class that tracks node health data.
"""
import logging
logger = logging.getLogger("gunicorn.error")

from collections import deque
from datetime import datetime, timedelta
from math import exp, inf, pow
import json
import os
import random
import shlex
import socket
import subprocess
import threading
import time


class NodeTracker:
    HEADINGS = ["Host", "Is Alive", "SCP Time (s)", "Uptime (%)", 
                "CPU (%)", "Memory (%)", "Last Update (GMT)"]
    SCP_TIMEOUT = 20  # seconds
    WAKE_TIMEOUT = 300  # 5 minutes
    PRUNE_INTERVAL = 15 * 60  # 15 minutes

    SCP_DECAY = 0.5

    @property
    def HOST_DATUM(self):
        return {
            "uptime": 0,
            "is_alive": False, 
            "scp_time": self.SCP_TIMEOUT * 2,
            "cpu": "0",
            "memory": "0",
            "last_update": datetime.now(),
        }

    def __init__(self, hosts_file, port, secret):
        with open(os.path.join("config", hosts_file)) as f:
            nodes = [x.strip() for x in f.readlines() if not x.isspace()]
        
        self.nodes = nodes
        if not len(nodes):
            raise ValueError

        # what's my ip?
        result = subprocess.run(shlex.split("curl https://ipinfo.io/ip"), 
                                stdout=subprocess.PIPE).stdout.decode('utf-8')
        self.public_ip = result.strip()
        self.port = port
        self.secret = secret

        # host history table, track # times host was up in the last 24 hours
        # key = hostname, val = queue of (time, boolean)
        self.history = {x: deque() for x in nodes}

        # key = hostname, val = host's health info
        self.node_health = {x: self.HOST_DATUM for x in nodes}

        # keep track of a living and dead nodes set
        self.living_nodes = set()
        self.dead_nodes = set(nodes)

        # this is necessary to synchronize updating operations
        # prevent report() returning nonsense data
        self.lock = threading.Lock()

        logger.info("Tracking %d nodes at (%s:%d)", len(nodes), 
                    self.public_ip, port)

#region public

    def start(self, wake_dead=True):
        """Start tracking the nodes."""
        # listening for heartbeats
        t_client = threading.Thread(target=self.__heartbeat_thread)
        t_client.start()

        # periodically recalculate the uptime
        t_prune = threading.Thread(target=self.__pruning_thread)
        t_prune.start()

        # periodically prod a dead node to see if it's woken up
        if wake_dead:
            t_wake = threading.Thread(target=self.__wakeup_thread)
            t_wake.start()

    def report(self):
        """Return a tuple (living, dead), where `living` is a 2D array
        containing the health data of all the living nodes, and `dead` is
        a list of dead nodes.

        A node is included in `living` if it is currently alive or has had
        uptime > 0 in the past day.
        """
        living = []
        dead = []

        self.lock.acquire()
        for host, datum in self.node_health.items():
            if datum['uptime'] == 0 and not datum['is_alive']:
                dead.append(host)
                continue

            is_alive = "yes" if datum['is_alive'] else "no"
            scp_time = "{:.2f}".format(datum['scp_time'])
            uptime = "{:.0f}".format(datum['uptime'] * 100)
            last_update = datum['last_update'].strftime("%H:%M %d-%m-%Y")
                
            living.append([host, is_alive, scp_time, uptime, 
                datum['cpu'], datum['memory'], last_update])

        self.lock.release()
        return living, dead

    def get_best_nodes(self, k):
        """Return a list of the k best nodes. The best nodes have the highest 
        uptime and the lowest scp time, in that order of importance.
        """
        if k > len(self.living_nodes):
            return list(self.living_nodes)

        def sort_key(x):
            healt = self.node_health[x]
            return (-health["uptime"], health["scp_time"])

        sorted_nodes = sorted(self.living_nodes, key=sort_key)
        return sorted_nodes[:k]

#endregion public
#region heartbeat

    def __heartbeat_thread(self):
        """Listen for heartbeats."""
        logger.debug("[Heartbeat] Thread started")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('', self.port))

        while True:
            raw, addr = sock.recvfrom(1024)  # should be big enough for data...
            logger.debug("[Heartbeat] Received: %s", (addr,))
            try:
                data = json.loads(raw.decode())
                t = threading.Thread(target=self.__handle_heartbeat,
                                     args=(data,))
                t.start()
            except:
                logger.info("[Heartbeat] Unparseable message: %s", (addr,))
                continue

    def __handle_heartbeat(self, data):
        """Parse a heartbeat message and perform the appropriate updates."""
        try:
            nodename = data["node"]
            cpu = float(data["cpu"])
            memory = float(data["memory"])
            health = self.node_health[nodename]
            assert(data["secret"] == self.secret)
        except KeyError:
            logger.warning("[Heartbeat] Malformed heartbeat message: %s", data)
            return
        except AssertionError:
            logger.warning("[Heartbeat] Wrong secret: %s", data)
            return
        except Exception as e:
            logger.warning("[Heartbeat] Unhandleable heartbeat: %s", data)
            return

        now = datetime.now()

        # check the node's scp time
        β = self.SCP_DECAY
        scp_time = health["scp_time"] * β + (1 - β) * self.__scp_time(nodename)

        self.lock.acquire()

        # make sure the node is correctly marked as alive
        self.living_nodes.add(nodename)
        self.dead_nodes.discard(nodename)

        # update node data
        health.update({
            "is_alive": True,
            "cpu": "{:.2}".format(cpu),
            "memory": "{:.2}".format(memory),
            "scp_time": scp_time,
            "last_update": now,
        })

        self.lock.release()
        logger.debug("[Heartbeat] Handled message from: %s", nodename)

#endregion heartbeat
#region wakethedead

    def __wakeup_thread(self):
        """Periodically check in on dead nodes to see if they've woken up."""
        logger.debug("[Wakeup] Thread started")

        while True:
            self.__try_wake()

            p_living = len(self.living_nodes) / len(self.nodes)

            # choose delay according to number of living nodes
            # with 10% alive, this will poke a new node every ~4 min
            # with 100% alive, check every 16 h in case one node has died
            delay = int(exp(11 * pow(p_living, 0.3)))
            time.sleep(delay)

    def __try_wake(self):
        """Poke a dead node. See if it responds."""
        try:
            lucky_winner = random.sample(self.dead_nodes, 1)[0]
        except (ValueError, IndexError):
            logger.warning("[Wakeup] Failed to get a dead node!")
            return

        # wake up the lucky winner -- this probably won't work :(
        cmd = ("ansible-playbook -i '{host},' playbooks/provision.yml \
               -u ubc_cpen431_1 -e 'aws_ip={ip} aws_port={port} secret={secret}' \
               --key-file=instance/planetlab.pem")
        cmd = cmd.format(host=lucky_winner, ip=self.public_ip, 
                         port=self.port, secret=self.secret)
        try:
            subprocess.run(shlex.split(cmd), check=True, 
                           timeout=self.WAKE_TIMEOUT)
        except subprocess.SubprocessError:
            logger.info("[Wakeup] Failed to wake node: %s", lucky_winner)
            return

        # wow it worked! test the scp time
        scp_time = self.__scp_time(lucky_winner)

        self.lock.acquire()

        self.living_nodes.add(lucky_winner)
        self.dead_nodes.discard(lucky_winner)
        self.node_health[lucky_winner].update({
            "last_update": datetime.now(),
            "is_alive": True,
            "scp_time": scp_time,
        })

        self.lock.release()
        logger.info("[Wakeup] Woke up node: %s", lucky_winner)

#endregion wakethedead
#region pruning

    def __pruning_thread(self):
        logger.debug("[Pruning] Thread started")
        while True:
            self.__prune_living()
            time.sleep(self.PRUNE_INTERVAL)

    def __prune_living(self):
        """Check the current alive/dead status for each node, add the info
        to the history table, and update node uptimes.

        If a node is "alive" but hasn't been heard from in over 1 hour,
        change it to dead.
        """
        if not len(self.living_nodes):
            return
        
        logger.debug("[Pruning] Starting a pruning pass")
        self.lock.acquire()

        now = datetime.now()
        for node, hist in self.history.items():
            health = self.node_health[node]
            if (health["is_alive"] and 
                    health["last_update"] + timedelta(hours=1) < now):
                # if the last update was too old, mark node as dead
                health.update({"is_alive": False, "last_update": now})
                self.living_nodes.discard(node)
                self.dead_nodes.add(node)
                logger.info("[Pruning] Demoted node: %s", node)
            
            # update history, prune off old entries
            hist.append((now, health["is_alive"]))
            while hist[0][0] + timedelta(days=1) < now:
                hist.popleft()

            # calculate uptime
            times_alive = sum([x[1] for x in hist])
            health["uptime"] = times_alive / len(hist)

        self.lock.release()

#endregion pruning
#region latency

    def __scp_time(self, nodename):
        """Report the time it takes to successfully SCP a small file to a 
        single host. If unsucessful or timed out, return SCP_TIMEOUT * 2.
        """
        scp_cmd = "scp -o StrictHostKeyChecking=no -i instance/planetlab.pem \
            resources/sonnets.txt ubc_cpen431_1@{host}:~".format(host=nodename)
        start_time = time.time()
        try:
            output = subprocess.run(shlex.split(scp_cmd), check=True, 
                                    timeout=self.SCP_TIMEOUT)
        except subprocess.SubprocessError:
            logger.debug("[SCP] Timed out: %s", nodename)
            return self.SCP_TIMEOUT * 3
        return time.time() - start_time

#endregion latency


if __name__ == '__main__':
    # test the module
    tracker = NodeTracker('testnodes.txt', 60001)
    tracker.PRUNE_INTERVAL = 100

    tracker.start()
    while True:
        print(*tracker.report())
        time.sleep(10)
