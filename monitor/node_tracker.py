"""
Stateful class that tracks node health data.
"""
from collections import deque
from datetime import datetime, timedelta
from math import exp, inf, pow
import os
import random
import shlex
import socket
import subprocess
import threading
import time


class NodeTracker:
    HEADINGS = ["Host", "Is Alive", "SSH Time (ms)", "SCP Time (ms)", 
            "Uptime (%)", "CPU (%)", "Memory (%)", "Last Update (GMT)"]
    TIMEOUT = 20  # how many seconds to wait for an SSH or SCP response
    PRUNE_INTERVAL = 15 * 60  # 15 minutes

    @property
    def HOST_DATUM(self):
        return {
            "uptime": 0, 
            "ssh_time": inf, 
            "is_alive": False, 
            "scp_time": inf,
            "cpu": "0",
            "memory": "0",
            "last_update": datetime.now(),
        }

    def __init__(self, hosts_file, port):
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

        # host history table, track # times host was up in the last 24 hours
        # key = hostname, val = queue of (time, boolean)
        self.history = {x: deque() for x in nodes}

        # key = hostname, val = host's health info
        self.node_health = {x: self.HOST_DATUM for x in nodes}

        # keep track of a living and dead nodes set
        self.living_nodes = set()
        self.dead_nodes = set(nodes)

        # this is necessary to synchronize updating operations
        # prevent get_output() returning nonsense data
        self.lock = threading.Lock()

#region public

    def start(self):
        """Start tracking the nodes."""
        # listening for heartbeats
        t_client = threading.Thread(target=self.__heartbeat_thread)
        t_client.start()

        # periodically prod a dead node to see if it's woken up
        t_wake = threading.Thread(target=self.__wakeup_thread)
        t_wake.start()

        # periodically recalculate the uptime
        t_prune = threading.Thread(target=self.__pruning_thread)
        t_prune.start()

    def get_output(self):
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
            ssh_time = "{:.2f}".format(datum['ssh_time'] * 1000)
            scp_time = "{:.2f}".format(datum['scp_time'] * 1000)
            uptime = "{:.0f}".format(datum['uptime'] * 100)
            last_update = datum['last_update'].strftime("%-I:%M %p %d-%m-%Y")
                
            living.append([host, is_alive, ssh_time, scp_time, uptime, 
                datum['cpu'], datum['memory'], last_update])

        self.lock.release()
        return living, dead

#endregion public
#region heartbeat

    def __heartbeat_thread(self):
        """Listen for heartbeats."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('', self.port))

        while True:
            raw, _ = sock.recvfrom(1024)  # should be big enough for data...
            try:
                data = json.loads(raw)
                t = threading.Thread(target=self.__handle_heartbeat,
                                     args=(data,))
                t.start()
            except:
                continue

    def __handle_heartbeat(self, data):
        """Parse a heartbeat message and perform the appropriate updates."""
        now = datetime.now()
        nodename = data["nodename"]

        # check the node's ssh & scp time
        ssh_time = self.__ssh_time(nodename)
        scp_time = self.__scp_time(nodename)

        self.lock.acquire()

        # make sure the node is correctly marked as alive
        self.living_nodes.add(nodename)
        self.dead_nodes.discard(nodename)

        # update node data
        self.node_health[nodename].update({
            "is_alive": True,
            "cpu": data["cpu"],
            "memory": data["memory"],
            "ssh_time": ssh_time,
            "scp_time": scp_time,
            "last_update": now,
        })

        self.lock.release()

        # clean up residuals
        if scp_time < inf:
            self.__cleanup_scp(nodename)

#endregion heartbeat
#region wakethedead

    def __wakeup_thread(self):
        """Periodically check in on dead nodes to see if they've woken up."""
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
            return

        # wake up the lucky winner -- this probably won't work :(
        cmd = ("ansible-playbook -i '{host},' playbooks/provision.yml \
               -u ubc_cpen431_1 -e 'aws_ip={ip} aws_port={port}' \
               --key-file=secret/planetlab.pem").format(
                   host=lucky_winner, ip=self.public_ip, port=self.port)
        try:
            # if it takes longer than this, we don't want the node anyway
            subprocess.run(shlex.split(cmd), check=True, timeout=500)
        except subprocess.SubprocessError:
            print("Failed to wake node: ", lucky_winner)
            return

        # wow it worked!
        print("Woke up node: ", lucky_winner)
        self.lock.acquire()

        self.living_nodes.add(lucky_winner)
        self.dead_nodes.discard(lucky_winner)
        self.node_health[lucky_winner].update({
            "last_update": datetime.now(),
            "is_alive": True,
        })

        self.lock.release()

#endregion wakethedead
#region pruning

    def __pruning_thread(self):
        while True:
            self.__prune_living()
            time.sleep(self.PRUNE_INTERVAL)

    def __prune_living(self):
        """Check the current alive/dead status for each node, add the info
        to the history table, and update node uptimes.

        If a node is "alive" but hasn't been heard from in over 1 hour,
        change it to dead.
        """
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

    def __ssh_time(self, nodename):
        """Report the time it takes to successfully SSH into a single host.
        If unsucessful or timed out, return math.inf.
        """
        cmd = "ansible all -i '{host},' --key-file secret/planetlab.pem \
            -m raw -a 'echo hello' -u ubc_cpen431_1".format(host=nodename)
        start_time = time.time()
        try:
            output = subprocess.run(shlex.split(cmd), check=True, 
                                                timeout=self.TIMEOUT)
        except subprocess.SubprocessError:
            return inf
        return time.time() - start_time

    def __scp_time(self, nodename):
        """Report the time it takes to successfully SSH into a single host.
        If unsucessful or timed out, return math.inf.
        """
        scp_cmd = "scp -i secret/planetlab.pem resources/sonnets.txt \
            ubc_cpen431_1@{host}:~".format(host=nodename)
        start_time = time.time()
        try:
            output = subprocess.run(shlex.split(scp_cmd), check=True, 
                                                timeout=self.TIMEOUT)
        except subprocess.SubprocessError:
            return inf
        return time.time() - start_time

    def __cleanup_scp(self, nodename):
        """Remove the previously SCPed file."""
        rm_cmd = "ansible all -i '{host},' --key-file secret/planetlab.pem \
            -m raw -a 'rm ~/sonnets.txt' -u ubc_cpen431_1".format(host=nodename)
        try:
            subprocess.run(shlex.split(rm_cmd), timeout=self.TIMEOUT)
        except subprocess.SubprocessError:
            pass

#endregion latency


if __name__ == '__main__':
    # test the module
    tracker = NodeTracker('testnodes.txt', 60001)
    tracker.PRUNE_INTERVAL = 30

    tracker.start()
    while True:
        print(*tracker.get_output())
        time.sleep(10)
